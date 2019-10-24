import torch
from transformers import XLNetTokenizer, XLNetModel, GPT2Tokenizer, GPT2Model, XLMTokenizer, XLMModel, BertTokenizer, BertModel, RobertaTokenizer, RobertaModel

import h5py
import json
import numpy as np
from tqdm import tqdm
import sys
import argparse 


def get_model_and_tokenizer(model_name, random_weights=False, model_path=None):
    """
    model_path: if given, initialize from path instead of official repo
    """

    init_model = model_name
    if model_path:
        print('Initializing model from local path:', model_path)
        init_model = model_path

    if model_name.startswith('xlnet'):
        model = XLNetModel.from_pretrained(init_model, output_hidden_states=True).to(device)
        tokenizer = XLNetTokenizer.from_pretrained(init_model)
        sep = u'▁'
    elif model_name.startswith('gpt2'):
        model = GPT2Model.from_pretrained(init_model, output_hidden_states=True).to(device)
        tokenizer = GPT2Tokenizer.from_pretrained(init_model)
        sep = 'Ġ'
    elif model_name.startswith('xlm'):
        model = XLMModel.from_pretrained(init_model, output_hidden_states=True).to(device)
        tokenizer = XLMTokenizer.from_pretrained(init_model)
        sep = '</w>'
    elif model_name.startswith('bert'):
        model = BertModel.from_pretrained(init_model, output_hidden_states=True).to(device)
        tokenizer = BertTokenizer.from_pretrained(init_model)
        sep = '##'
    elif model_name.startswith('roberta'):
        model = RobertaModel.from_pretrained(model_name, output_hidden_states=True).to(device)
        tokenizer = RobertaTokenizer.from_pretrained(model_name)
        sep = 'Ġ'        
    else:
        print('Unrecognized model name:', model_name)
        sys.exit()

    if random_weights:
        print('Randomizing weights')
        model.apply(model.init_weights)

    return model, tokenizer, sep

# this follows the HuggingFace API for pytorch-transformers
def get_sentence_repr(sentence, model, tokenizer, sep, model_name):
    """
    Get representations for one sentence
    """

    with torch.no_grad():
        ids = tokenizer.encode(sentence)
        input_ids = torch.tensor([ids]).to(device)
        # Hugging Face format: list of torch.FloatTensor of shape (batch_size, sequence_length, hidden_size) (hidden_states at output of each layer plus initial embedding outputs)
        all_hidden_states = model(input_ids)[-1]
        # convert to format required for contexteval: numpy array of shape (num_layers, sequence_length, representation_dim)
        all_hidden_states = [hidden_states[0].cpu().numpy() for hidden_states in all_hidden_states[:-1]]
        all_hidden_states = np.array(all_hidden_states)

    #For each word, take the representation of its last sub-word
    segmented_tokens = tokenizer.convert_ids_to_tokens(ids)
    assert len(segmented_tokens) == all_hidden_states.shape[1], 'incompatible tokens and states'
    mask = np.full(len(segmented_tokens), False)

    if model_name.startswith('gpt2') or model_name.startswith('xlnet') or model_name.startswith('roberta'):
        # if next token is a new word, take current token's representation
        #print(segmented_tokens)
        for i in range(len(segmented_tokens)-1):
            if segmented_tokens[i+1].startswith(sep):
                #print(i)
                mask[i] = True
        # always take the last token representation for the last word
        mask[-1] = True
    # example: ['jim</w>', 'henson</w>', 'was</w>', 'a</w>', 'pup', 'pe', 'teer</w>']
    elif model_name.startswith('xlm'):
        # if current token is a new word, take it
        for i in range(len(segmented_tokens)):
            if segmented_tokens[i].endswith(sep):
                mask[i] = True
        mask[-1] = True
    elif model_name.startswith('bert'):
        # if next token is not a continuation, take current token's representation
        for i in range(len(segmented_tokens)-1):
            if not segmented_tokens[i+1].startswith(sep):
                mask[i] = True
        mask[-1] = True
    else:
        print('Unrecognized model name:', model_name)
        sys.exit()

    all_hidden_states = all_hidden_states[:, mask]

    return all_hidden_states


def get_sentences_from_hdf5(hdf5_filename):

    hdf5 = h5py.File(hdf5_filename)
    sentence_to_idx = json.loads(hdf5['sentence_to_idx'][0])
    sentences = []
    for s in sentence_to_idx:
        sentences.append(s)

    return sentences, sentence_to_idx


# from https://github.com/nelson-liu/contextual-repr-analysis
def make_hdf5_file(sentence_to_index, vectors, output_file_path):
    with h5py.File(output_file_path, 'w') as fout:
        for key, embeddings in vectors.items():
            fout.create_dataset(
                str(key),
                embeddings.shape, dtype='float32',
                data=embeddings)
        sentence_index_dataset = fout.create_dataset(
            "sentence_to_index",
            (1,),
            dtype=h5py.special_dtype(vlen=str))
        sentence_index_dataset[0] = json.dumps(sentence_to_index)


def run(input_hdf5_filename, model, tokenizer, sep, output_hdf5_filename, model_name):

    print('reading sentences from hdf5')
    hdf5 = h5py.File(input_hdf5_filename)
    sentence_to_idx = json.loads(hdf5['sentence_to_index'][0])
    hdf5.close()
    idx_to_repr = dict()

    print('getting repreesntations from model')
    for s, idx in tqdm(sentence_to_idx.items(), desc='repr'):
        hidden_states = get_sentence_repr(s, model, tokenizer, sep, model_name)
        idx_to_repr[idx] = hidden_states

    print('writing representations to new hdf5')
    make_hdf5_file(sentence_to_idx, idx_to_repr, output_hdf5_filename)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("model_name")
    parser.add_argument("input_hdf5_file")
    parser.add_argument("output_hdf5_file")
    parser.add_argument("--random_weights", action="store_true", help="generate representations from randomly initialized model")
    parser.add_argument("--model_path", help="Local path to load model from", default=None)
    parser.add_argument("--disable_cuda", action="store_true")
    args = parser.parse_args()
    print(args)

    if not args.disable_cuda and torch.cuda.is_available():
        device = torch.device('cuda')
    else:
        device = torch.device('cpu')


    model, tokenizer, sep = get_model_and_tokenizer(args.model_name, random_weights=args.random_weights, model_path=args.model_path)
    run(args.input_hdf5_file, model, tokenizer, sep, args.output_hdf5_file, args.model_name)



