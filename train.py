import json
import os
import re
import time
import argparse
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim

os.environ["CUDA_VISIBLE_DEVICES"] = "2"
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

from model import CDA_Graph
from dataloader import load_info_data, load_pre_process
from utils import accuracy, precision, recall, specificity, auc, aupr, f1
from gcn_covid import smile_to_graph, TestbedDataset, GCNNet, DataLoader


###############################################################
# Training settings
parser = argparse.ArgumentParser(description='CDI-GRAPH')
parser.add_argument('--seed', type=int, default=1, help='Random seed.') # 3:4 ; 4:1
parser.add_argument('--epochs', type=int, default=3500,
                    help='Number of epochs to train.')
parser.add_argument('--lr', type=float, default=0.00005, # 0.00005
                    help='Initial learning rate.')
parser.add_argument('--weight_decay', type=float, default=5e-4,
                    help='Weight decay (L2 loss on parameters).')
parser.add_argument('--dropout', type=float, default=0.3,
                    help='Dropout rate (1 - keep probability).')
parser.add_argument('--model_dir', type=str, default='./trained_model',
                    help='model save path')
parser.add_argument('--crossvalidation', type=int, default=5,
                    help='whether use crossvalidation or not')
###############################################################
# Model hyper setting
# CircRNA_NN
parser.add_argument('--circRNA_ninput', type=int, default=256,
                    help='circRNA vector size')
parser.add_argument('--cnn_nlayers', type=int, default=1,
                    help='CircRNA_nn layers num')
parser.add_argument('--cnn_nhid', type=str, default='[]',
                    help='pnn hidden layer dim, like [200,100] for tow hidden layers')
# Drug_NN
parser.add_argument('--drug_ninput', type=int, default=256,
                    help='Drug fingerprint dimension')
parser.add_argument('--dnn_nlayers', type=int, default=1,
                    help='dnn_nlayers num')
parser.add_argument('--dnn_nhid', type=str, default='[]',
                    help='dnn hidden layer dim, like [200,100] for tow hidden layers')
# GAT
parser.add_argument('--gat_type', type=str, default='PyG',
                    help="two different type, 'PyG Sparse GAT'(PyG) and 'Dense GAT Self'(Dense-Self)")
parser.add_argument('--gat_ninput', type=int, default=256,
                    help='GAT node feature length, is also the pnn  output size and dnn output size')
parser.add_argument('--gat_nhid', type=int, default=32,
                    help='hidden dim of gat')
parser.add_argument('--gat_noutput', type=int, default=256,
                    help='GAT output feature dim and the input dim of Decoder')
parser.add_argument('--gat_nheads', type=int, default=3,
                    help='GAT layers')
parser.add_argument('--gat_negative_slope', type=float, default=0.5,
                    help='GAT LeakyReLU angle of the negative slope.')
# Decoder
parser.add_argument('--CDA_nn_nlayers', type=int, default=3,
                    help='Protein_nn layers num')
parser.add_argument('--CDA_nn_nhid', type=list, default=[512,512,512],
                    help='DTI_nn hidden layer dim, like [200,100] for tow hidden layers')
###############################################################
# data
parser.add_argument('--dataset', type=str, default='kfold',
                    help='dataset name')
parser.add_argument('--data_path', type=str, default='./five_ten_cv_data/five',
                    help='dataset root path')
args = parser.parse_args()
args.cuda = torch.cuda.is_available()
# seeds
np.random.seed(args.seed)
torch.manual_seed(args.seed)
if args.cuda:
    torch.cuda.manual_seed(args.seed)
# save dir
if not os.path.exists(args.model_dir):
    os.mkdir(args.model_dir)

# Hyper Setting
    
cnn_hyper = [args.circRNA_ninput, args.cnn_nhid, args.gat_ninput, args.cnn_nlayers]
dnn_hyper = [args.drug_ninput, args.dnn_nhid, args.gat_ninput, args.dnn_nlayers]
GAT_hyper = [args.gat_ninput, args.gat_nhid, args.gat_noutput, args.gat_negative_slope, args.gat_nheads]
Deco_hyper = [args.gat_noutput, args.CDA_nn_nhid, args.CDA_nn_nlayers]

def train(epoch, link_cdi_id_train, edge_index, train_cdi_inter_mat,circRNA_tensor, drug_tensor):
    # if use PyG's sparse gcn, you will need the edge_weight
    t = time.time()
    model.train()
    optimizer.zero_grad()
    row_cdi_id = link_cdi_id_train.permute(1, 0)[0] # 6614 circRNA
    col_cdi_id = link_cdi_id_train.permute(1, 0)[1] # 6614 drug
    circRNA_index = row_cdi_id
    drug_index = col_cdi_id + train_cdi_inter_mat.shape[0] # + 271(number of circRNA)
    for batch_idx, data in enumerate(drugdata):
        data1 = data.to(device)
        output = model(circRNA_tensor, drug_tensor, edge_index, circRNA_index, drug_index, data1)
    Loss = nn.BCELoss()
    loss_train = Loss(output, train_cdi_inter_mat[row_cdi_id, col_cdi_id])
    acc_cdi_train = accuracy(output, train_cdi_inter_mat[row_cdi_id, col_cdi_id])
    loss_train.backward()
    optimizer.step()
    print('Epoch {:04d} Train '.format(epoch + 1),
          'loss_train: {:.4f}'.format(loss_train.item()),
          'acc_dti_train: {:.4f}'.format(acc_cdi_train),
          'time: {:.4f}s'.format(time.time() - t))

def test(link_cdi_id_test, edge_index, test_cdi_inter_mat):
    # if use PyG's sparse gcn, you will need the edge_weight
    model.eval()
    row_cdi_id = link_cdi_id_test.permute(1, 0)[0]
    col_cdi_id = link_cdi_id_test.permute(1, 0)[1]
    circRNA_index = row_cdi_id
    drug_index = col_cdi_id + test_cdi_inter_mat.shape[0]
    for batch_idx, data in enumerate(drugdata): 
        data1 = data.to(device)   
        output = model(circRNA_tensor, drug_tensor, edge_index, circRNA_index, drug_index, data1)
    Loss = nn.BCELoss()
    predicts = output
    targets = test_cdi_inter_mat[row_cdi_id, col_cdi_id]
    loss_test = Loss(predicts, targets)
    acc_cdi_test = accuracy(output, test_cdi_inter_mat[row_cdi_id, col_cdi_id])
    return acc_cdi_test, loss_test, predicts, targets

# Train model

for i in range(args.crossvalidation):

    # load data
    data_Path = os.path.join(args.data_path, 'data_'+args.dataset+str(i)+'.npz')
    circRNA_tensor, drug_tensor, node_num, circRNA_num = load_info_data(data_Path) # [271,489] [218,489] 489 271

    preprocess_oripath = os.path.join(args.data_path, '0_'+str(i)+'.json')  # data path
    adj, cdi_inter_mat, train_interact_pos, val_interact_pos = load_pre_process(preprocess_oripath) # prepared data [489,489] [271,218] [6614,2] [1654,2]
    edge_index = torch.nonzero(adj > 0).permute(1, 0)
    edge_weight = adj[np.array(edge_index)]

    smiles = np.load("./data/drug_smile.npy") # 218

    # get smiles for GCN
    compound_iso_smiles = []
    compound_iso_smiles += list(smiles)
    compound_iso_smiles = set(compound_iso_smiles)
    smile_graph = {}

    for smile in compound_iso_smiles:
        g = smile_to_graph(smile)
        smile_graph[smile] = g

    df_smiles = pd.DataFrame(smiles)
    drugdata = TestbedDataset( xd=df_smiles[0], y= [0 for _ in range(218)], smile_graph=smile_graph)
    drugdata = DataLoader(drugdata, batch_size=218, shuffle=None)

    model = CDA_Graph(GAT_hyper=GAT_hyper, CNN_hyper=cnn_hyper, DNN_hyper=dnn_hyper, DECO_hyper=Deco_hyper,
                      CircRNA_num=circRNA_tensor.shape[0], Drug_num=drug_tensor.shape[0], dropout=args.dropout, smiles_gcn= GCNNet().to(device))
    optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    if args.cuda:
        model = model.cuda()
        circRNA_tensor = circRNA_tensor.cuda()
        drug_tensor = drug_tensor.cuda()
        edge_index = edge_index.cuda()
        edge_weight = edge_weight.cuda() # if you want to use gcn and so on
        cdi_inter_mat = cdi_inter_mat.cuda()
        train_interact_pos = train_interact_pos.cuda()
        val_interact_pos = val_interact_pos.cuda()

    max_auc = 0
    max_aupr = 0
    max_precision = 0
    max_recall = 0
    max_specificity = 0
    max_f1 = 0
    max_acc = 0
    for epoch in range(args.epochs):
        train(epoch, train_interact_pos, edge_index, cdi_inter_mat, circRNA_tensor, drug_tensor)
        test_score, test_loss, predicts, targets = test(val_interact_pos, edge_index, cdi_inter_mat)
        auc_score = round(auc(predicts, targets), 4)
        aupr_score = round(aupr(predicts, targets), 4)
        precision_score = round(precision(predicts, targets), 4)
        recall_score = round(recall(predicts, targets), 4)
        specificity_score = round(specificity(predicts, targets), 4)
        f1_score = round(f1(predicts, targets), 4)
        if auc_score > max_auc:
            predict_target = torch.cat((predicts, targets), dim=0).detach().cpu().numpy()
            precision_score = round(precision(predicts, targets), 4)
            recall_score = round(recall(predicts, targets), 4)
            specificity_score = round(specificity(predicts, targets), 4)
            f1_score = round(f1(predicts, targets), 4)
            auc_score = round(auc(predicts, targets), 4)
            aupr_score = round(aupr(predicts, targets), 4)
            acc_score = round(accuracy(predicts, targets), 4)

            max_auc = auc_score
            max_aupr = aupr_score
            max_recall = recall_score
            max_precision = precision_score
            max_specificity = specificity_score
            max_f1 = f1_score
            max_acc = acc_score

    print("acc Score:", max_acc)
    print("precision Score:", max_precision)
    print("recall score", max_recall)
    print("specificity score", max_specificity)
    print("f1 score", max_f1)
    print("auc socre", max_auc)
    print("aupr score", max_aupr)
    print("Best Ave Test: {:.4f}".format(np.mean(acc_score)))
with open(os.path.join(args.model_dir, 'documen.txt'), 'w') as f:
    f.write('acc ' + str(acc_score) + '\n')
    f.write('prec ' + str(precision_score) + '\n')
    f.write('recall ' + str(recall_score) + '\n')
    f.write('spec ' + str(specificity_score) + '\n')
    f.write('f1 ' + str(f1_score) + '\n')
    f.write('auc ' + str(auc_score) + '\n')
    f.write('aupr ' + str(aupr_score) + '\n')
    f.write('best ave acc ' + str(np.mean(acc_score)))