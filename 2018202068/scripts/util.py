#! -*- coding:utf-8 -*-
import numpy as np
import random
import copy
import os
import pickle
import torch
from data_utils import DataGenerator

def print_config(args):
    config_path=os.path.join(args.output_path,"config.txt")
    with open(config_path,"w",encoding="utf-8") as f:
        for k,v in sorted(vars(args).items()):
            print(k,'=',v,file=f)

def set_seed():

    random.seed(0)
    np.random.seed(0)
    torch.manual_seed(0)

def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        pass

    try:
        import unicodedata
        unicodedata.numeric(s)
        return True
    except (TypeError, ValueError):
        pass

    return False

def mat_padding(inputs, length=None, padding=0):

    if not type(inputs[0]) is np.ndarray:
        inputs = [np.array(i) for i in inputs]

    if length is None:
        length = max([x.shape[0] for x in inputs])

    pad_width = [(0, 0) for _ in np.shape(inputs[0])]

    outputs = []
    for x in inputs:
        pad_width[0] = (0, length - x.shape[0])
        pad_width[1] = (0, length - x.shape[0])
        x = np.pad(x, pad_width, 'constant', constant_values=padding)
        outputs.append(x)
    return np.array(outputs)

def tuple_mat_padding(inputs,dim=1, length=None, padding=0):

    if not type(inputs[0]) is np.ndarray:
        inputs = [np.array(i) for i in inputs]

    if length is None:
        length = max([x.shape[dim] for x in inputs])
    pad_width = [(0, 0) for _ in np.shape(inputs[0])]
    outputs = []
    for x in inputs:
        pad_width[1] = (0, length - x.shape[dim])
        pad_width[2] = (0, length - x.shape[dim])
        x = np.pad(x, pad_width, 'constant', constant_values=padding)
        outputs.append(x)
    return np.array(outputs)

def sequence_padding(inputs,dim=0, length=None, padding=0):
    if not type(inputs[0]) is np.ndarray:
        inputs = [np.array(i) for i in inputs]

    if length is None:
        length = max([x.shape[dim] for x in inputs])
    pad_width = [(0, 0) for _ in np.shape(inputs[0])]
    outputs = []
    for x in inputs:
        pad_width[dim] = (0, length - x.shape[dim])
        x = np.pad(x, pad_width, 'constant', constant_values=padding)
        outputs.append(x)
    return np.array(outputs)

def judge(ex):
    for s, _, o in ex["spos"]:
        if s[-1] == '' or o[-1] == '' or s[-1] not in ex["text"] or o[-1] not in ex["text"]:
            return False
    return True

def extract_spo_list(args, tokenizer, id2predicate, id2label, label2id, model, batch_ex, batch_token_ids, batch_mask):

    if isinstance(model, torch.nn.DataParallel):
        model = model.module
    model.to("cuda")
    model.eval()

    with torch.no_grad():
        table = model(batch_token_ids, batch_mask)
        table = table.cpu().detach().numpy()

    def get_pred_id(table, all_tokens):

        B, L, _, R, _ = table.shape
        res = []
        for i in range(B):
            res.append([])
        table = table.argmax(axis=-1)  # BLLR
        all_loc = np.where(table != label2id["N/A"])
        res_dict = []
        for i in range(B):
            res_dict.append([])
        for i in range(len(all_loc[0])):
            token_n = len(all_tokens[all_loc[0][i]])
            if token_n - 1 <= all_loc[1][i] \
                    or token_n - 1 <= all_loc[2][i] \
                    or 0 in [all_loc[1][i], all_loc[2][i]]:
                continue
            res_dict[all_loc[0][i]].append([all_loc[1][i], all_loc[2][i], all_loc[3][i]])

        for i in range(B):
            for l1, l2, r in res_dict[i]:
                if table[i, l1, l2, r] == label2id["SS"]:
                    res[i].append([l1, l1, r, l2, l2])
                elif table[i, l1, l2, r] == label2id["SMH"]:
                    for l1_, l2_, r_ in res_dict[i]:
                        if r == r_ and table[i, l1_, l2_, r_] == label2id[
                            "SMT"] and l1_ == l1 and l2_ > l2:
                            res[i].append([l1, l1, r, l2, l2_])
                            break
                elif table[i, l1, l2, r] == label2id["MMH"]:
                    for l1_, l2_, r_ in res_dict[i]:
                        if r == r_ and table[i, l1_, l2_, r_] == label2id[
                            "MMT"] and l1_ > l1 and l2_ > l2:
                            res[i].append([l1, l1_, r, l2, l2_])
                            break
                elif table[i, l1, l2, r] == label2id["MSH"]:
                    for l1_, l2_, r_ in res_dict[i]:
                        if r == r_ and table[i, l1_, l2_, r_] == label2id[
                            "MST"] and l1_ > l1 and l2_ == l2:
                            res[i].append([l1, l1_, r, l2, l2_])
                            break
        return res

    all_tokens = []
    for ex in batch_ex:
        tokens = tokenizer.tokenize(ex["text"], maxlen=args.max_len)
        all_tokens.append(tokens)

    res_id = get_pred_id(table, all_tokens)
    batch_spo = [[] for _ in range(len(batch_ex))]
    for b, ex in enumerate(batch_ex):
        text = ex["text"]
        tokens = all_tokens[b]
        mapping = tokenizer.rematch(text, tokens)
        for sh, st, r, oh, ot in res_id[b]:
            s = (mapping[sh][0], mapping[st][-1])
            o = (mapping[oh][0], mapping[ot][-1])
            batch_spo[b].append(
                ((s[0], s[1] + 1, text[s[0]:s[1] + 1]), id2predicate[str(r)], (o[0], o[1] + 1, text[o[0]:o[1] + 1])) #？
            )
    return batch_spo

def get_token_idx(text, entities, tokenizer):
    one = {}
    for idx, ent in enumerate(entities):
        s_idx = ent[0]
        e_idx = ent[1]
        prefix_text = text[:s_idx]
        suffix_text = text[s_idx:e_idx]
        key = (s_idx, e_idx)
        token_ids1, mask = tokenizer.encode(prefix_text, maxlen=200)
        token_ids2, mask = tokenizer.encode(suffix_text, maxlen=200)
        start_token_idx = len(token_ids1)-1
        token_idxs = [start_token_idx, start_token_idx+len(token_ids2) - 2 - 1]
        one[key] = token_idxs
    return one

class data_generator(DataGenerator):
    def __init__(self, args, train_data, tokenizer, predicate_map, label_map, batch_size, random=False, is_train=True):
        super(data_generator, self).__init__(train_data, batch_size)
        self.max_len = args.max_len
        self.tokenizer = tokenizer
        self.predicate2id, self.id2predicate = predicate_map
        self.label2id, self.id2label = label_map
        self.random = random
        self.is_train = is_train

    def __iter__(self):
        batch_token_ids, batch_mask = [], []
        batch_label = []
        batch_mask_label = []
        batch_ex = []
        for is_end, d in self.sample(self.random):
            if self.is_train:
                if judge(d) == False:
                    continue
            token_ids, mask = self.tokenizer.encode(
                d['text'], maxlen=self.max_len
            )
            if self.is_train:
                entities = []
                for spo in d['spos']:
                    entities.append(tuple(spo[0]))
                    entities.append(tuple(spo[2]))
                entities = sorted(list(set(entities)))
                one_info = get_token_idx(d['text'], entities, self.tokenizer)
                spoes = {}
                for ss, pp, oo in d['spos']:
                    s_key = (ss[0], ss[1])
                    p = self.predicate2id[pp]
                    o_key = (oo[0], oo[1])
                    s = tuple(one_info[s_key])
                    o = copy.deepcopy(one_info[o_key])
                    o.append(p)
                    o = tuple(o)
                    if s not in spoes:
                        spoes[s] = []
                    spoes[s].append(o)

                if spoes:
                    label = np.zeros([len(token_ids), len(token_ids), len(self.id2predicate)])
                    for s in spoes:
                        s1, s2 = s
                        try:
                            for o1, o2, p in spoes[s]:
                                try:
                                    if s1 == s2 and o1 == o2:
                                        label[s1, o1, p] = self.label2id["SS"]
                                    elif s1 != s2 and o1 == o2:
                                        label[s1, o1, p] = self.label2id["MSH"]
                                        label[s2, o1, p] = self.label2id["MST"]
                                    elif s1 == s2 and o1 != o2:
                                        label[s1, o1, p] = self.label2id["SMH"]
                                        label[s1, o2, p] = self.label2id["SMT"]
                                    elif s1 != s2 and o1 != o2:
                                        label[s1, o1, p] = self.label2id["MMH"]
                                        label[s2, o2, p] = self.label2id["MMT"]
                                except:

                                    print(d, spoes)
                        except Exception as e:
                            print(one_info, d['text'])
                            assert 0

                    mask_label = np.ones(label.shape)
                    mask_label[0, :, :] = 0
                    mask_label[-1, :, :] = 0
                    mask_label[:, 0, :] = 0
                    mask_label[:, -1, :] = 0

                    for a, b in zip([batch_token_ids, batch_mask, batch_label, batch_mask_label, batch_ex],
                                    [token_ids, mask, label, mask_label, d]):
                        a.append(b)

                    if len(batch_token_ids) == self.batch_size or is_end:
                        batch_token_ids, batch_mask = [sequence_padding(i) for i in [batch_token_ids, batch_mask]]
                        batch_label = mat_padding(batch_label)
                        batch_mask_label = mat_padding(batch_mask_label)
                        yield [
                            batch_token_ids, batch_mask,
                            batch_label,
                            batch_mask_label, batch_ex
                        ]
                        batch_token_ids, batch_mask = [], []
                        batch_label = []
                        batch_mask_label = []
                        batch_ex = []
            else:
                for a, b in zip([batch_token_ids, batch_mask, batch_ex], [token_ids, mask, d]):
                    a.append(b)
                if len(batch_token_ids) == self.batch_size or is_end:
                    batch_token_ids, batch_mask = [sequence_padding(i) for i in [batch_token_ids, batch_mask]]
                    yield [
                        batch_token_ids, batch_mask, batch_ex
                    ]
                    batch_token_ids, batch_mask = [], []
                    batch_ex = []

class Vocab(object):
    def __init__(self, filename, load=False, word_counter=None, threshold=0):
        if load:
            assert os.path.exists(filename), "Vocab file does not exist at " + filename
            # load from file and ignore all other params
            self.id2word, self.word2id = self.load(filename)
            self.size = len(self.id2word)
            print("Vocab size {} loaded from file".format(self.size))
        else:
            print("Creating vocab from scratch...")
            assert word_counter is not None, "word_counter is not provided for vocab creation."
            self.word_counter = word_counter
            if threshold > 1:
                # remove words that occur less than thres
                self.word_counter = dict([(k, v) for k, v in self.word_counter.items() if v >= threshold])
            self.id2word = sorted(self.word_counter, key=lambda k: self.word_counter[k], reverse=True)
            # add special tokens to the beginning
            self.id2word = ['**PAD**', '**UNK**'] + self.id2word
            self.word2id = dict([(self.id2word[idx], idx) for idx in range(len(self.id2word))])
            self.size = len(self.id2word)
            self.save(filename)
            print("Vocab size {} saved to file {}".format(self.size, filename))

    def load(self, filename):
        with open(filename, 'rb') as infile:
            id2word = pickle.load(infile)
            word2id = dict([(id2word[idx], idx) for idx in range(len(id2word))])
        return id2word, word2id

    def save(self, filename):
        # assert not os.path.exists(filename), "Cannot save vocab: file exists at " + filename
        if os.path.exists(filename):
            print("Overwriting old vocab file at " + filename)
            os.remove(filename)
        with open(filename, 'wb') as outfile:
            pickle.dump(self.id2word, outfile)
        return

    def map(self, token_list):
        """
        Map a list of tokens to their ids.
        """
        return [self.word2id[w] if w in self.word2id else constant.VOCAB_UNK_ID for w in token_list]

    def unmap(self, idx_list):
        """
        Unmap ids back to tokens.
        """
        return [self.id2word[idx] for idx in idx_list]

    def get_embeddings(self, word_vectors=None, dim=100):
        # self.embeddings = 2 * constant.EMB_INIT_RANGE * np.random.rand(self.size, dim) - constant.EMB_INIT_RANGE
        self.embeddings = np.zeros((self.size, dim))
        if word_vectors is not None:
            assert len(list(word_vectors.values())[0]) == dim, \
                "Word vectors does not have required dimension {}.".format(dim)
            for w, idx in self.word2id.items():
                if w in word_vectors:
                    self.embeddings[idx] = np.asarray(word_vectors[w])
        return self.embeddings
