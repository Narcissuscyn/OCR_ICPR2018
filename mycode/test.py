
from __future__ import print_function
import argparse
import random
import torch
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
from torch.autograd import Variable
import numpy as np
from warpctc_pytorch import CTCLoss
import os
import utils
import dataset
import chardet
import keys
# import sys
import collections
# import importlib,sys
# importlib.reload(sys)
# sys.setdefaultencoding('utf8')

import models.crnn as crnn
os.environ["CUDA_VISIBLE_DEVICES"] ="1"
str1 = keys.alphabet
parser = argparse.ArgumentParser()
parser.add_argument('--trainroot', help='path to dataset', default='../tool/train_cmp/')
parser.add_argument('--valroot', help='path to dataset', default='../tool/final_test/')
parser.add_argument('--workers', type=int, help='number of data loading workers', default=4)
parser.add_argument('--batchSize', type=int, default=64, help='input batch size')
parser.add_argument('--imgH', type=int, default=32, help='the height of the input image to network')
parser.add_argument('--imgW', type=int, default=300, help='the width of the input image to network')
parser.add_argument('--nh', type=int, default=512, help='size of the lstm hidden state')
parser.add_argument('--niter', type=int, default=600, help='number of epochs to train for')
parser.add_argument('--lr', type=float, default=0.0001, help='learning rate for Critic, default=0.00005')
parser.add_argument('--beta1', type=float, default=0.5, help='beta1 for adam. default=0.5')
parser.add_argument('--cuda', action='store_true', help='enables cuda', default=True)
parser.add_argument('--ngpu', type=int, default=1, help='number of GPUs to use')
parser.add_argument('--crnn', default='', help="path to crnn (to continue training)")
parser.add_argument('--alphabet', type=str, default=str1)
parser.add_argument('--Diters', type=int, default=5, help='number of D iters per each G iter')
parser.add_argument('--experiment', default=None, help='Where to store samples and models')
parser.add_argument('--displayInterval', type=int, default=400, help='Interval to be displayed')
parser.add_argument('--n_test_disp', type=int, default=50, help='Number of samples to display when test')
parser.add_argument('--valInterval', type=int, default=10, help='Interval to be displayed')
parser.add_argument('--saveInterval', type=int, default=10, help='Interval to be displayed')
parser.add_argument('--adam', action='store_true', help='Whether to use adam (default is rmsprop)', default=False)
parser.add_argument('--adadelta', action='store_true', help='Whether to use adadelta (default is rmsprop)',
                    default=False)
parser.add_argument('--keep_ratio', action='store_true', help='whether to keep ratio for image resize', default=True)
parser.add_argument('--random_sample', action='store_true', help='whether to sample the dataset with random sampler',
                    default=False)
opt = parser.parse_args()
# print(opt)
print(opt)
# test_dataset = dataset.lmdbDataset(
#     root=opt.valroot, transform=dataset.resizeNormalize((opt.imgW, opt.imgH)))

test_dataset = dataset.lmdbDataset(root=opt.valroot)
# test_dataset = dataset.lmdbDataset(
#     root=opt.valroot, transform=None)
model_path = '../expr1/netCRNN_79_2679.pth'

alphabet=opt.alphabet
if torch.cuda.is_available() and not opt.cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")

converter = utils.strLabelConverter(alphabet)
image = torch.FloatTensor(opt.batchSize, 3, opt.imgH, opt.imgH)
text = torch.IntTensor(opt.batchSize * 5)
length = torch.IntTensor(opt.batchSize)
criterion = CTCLoss()

nclass = len(alphabet)+1
pre_model = torch.load(model_path)

model = crnn.CRNN(opt.imgH, 1, nclass, opt.nh)
print('loading pretrained model from %s' % model_path)

mymodel={}
for k,v in pre_model.items():
    mymodel[k[7:]]=v
    print(k,len(v))

model.load_state_dict(mymodel)


if opt.cuda:
    model.cuda()
    model = torch.nn.DataParallel(model, device_ids=range(opt.ngpu))
    image = image.cuda()
    criterion = criterion.cuda()
image = Variable(image)
text = Variable(text)
length = Variable(length)


def val(net, val_dataset, criterion, max_iter=100):
    print('Start val')

    for p in model.parameters():
        p.requires_grad = False

    net.eval()
    data_loader = torch.utils.data.DataLoader(
        val_dataset, shuffle=False, batch_size=opt.batchSize, num_workers=int(opt.workers),collate_fn=dataset.alignCollate(imgH=opt.imgH, imgW=opt.imgW, keep_ratio=opt.keep_ratio))
    val_iter = iter(data_loader)

    i = 0
    n_correct = 0
    loss_avg = utils.averager()

    max_iter = max(max_iter, len(data_loader))

    img_num=1
    str_line=''
    dst_file=open('/home/new/File/OCR/crnn/mycode/test_rst.txt','w')
    dst_root='/home/new/File/OCR/crnn/mycode/test_result/'
    for i in range(max_iter):
        data = val_iter.next()
        i += 1
        cpu_images, cpu_texts = data
        batch_size = cpu_images.size(0)
        utils.loadData(image, cpu_images)
        t, l = converter.encode(cpu_texts)
        utils.loadData(text, t)
        utils.loadData(length, l)

        preds = model(image)
        preds_size = Variable(torch.IntTensor([preds.size(0)] * batch_size))
        cost = criterion(preds, text, preds_size, length) / batch_size
        loss_avg.add(cost)

        _, preds = preds.max(2)
        preds = preds.transpose(1, 0).contiguous().view(-1)
        sim_preds = converter.decode(preds.data, preds_size.data, raw=False)

        for pred, gt in zip(sim_preds, cpu_texts):
            #with subcribe
            # if int(gt[5:-4])==img_num:
            #     str_line+=pred
            # else:
            #
            #     # dst_ite_pth=dst_root+str(img_num).zfill(7)+'.txt'
            #     # f=open(dst_ite_pth,'w+')
            #     # f.write(str_line)
            #     # f.close()
            #     dst_file.write(gt+' '+str_line+'\n')
            #     str_line=pred
            #     img_num+=1

            #without subcribe
            dst_file.write(gt + ' ' + pred + '\n')

            print('pred:%-20s, gt: %-20s' % ( pred, gt))
    dst_file.close()
val(model, test_dataset, criterion)

