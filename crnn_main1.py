# coding: utf-8
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

import models.crnn1 as crnn
# from torchviz import make_dot
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
str1 = keys.alphabet
parser = argparse.ArgumentParser()
parser.add_argument('--trainroot', help='path to dataset', default='./tool/train_lmdb/')
parser.add_argument('--valroot', help='path to dataset', default='./tool/val_lmdb/')
parser.add_argument('--workers', type=int, help='number of data loading workers', default=4)
parser.add_argument('--batchSize', type=int, default=64, help='input batch size')
parser.add_argument('--imgH', type=int, default=32, help='the height of the input image to network')
parser.add_argument('--imgW', type=int, default=320, help='the width of the input image to network')
parser.add_argument('--nh', type=int, default=256, help='size of the lstm hidden state')
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
parser.add_argument('--n_test_disp', type=int, default=10, help='Number of samples to display when test')
parser.add_argument('--valInterval', type=int, default=5, help='Interval to be displayed')
parser.add_argument('--saveInterval', type=int, default=5, help='Interval to be displayed')
parser.add_argument('--adam', action='store_true', help='Whether to use adam (default is rmsprop)', default=False)
parser.add_argument('--adadelta', action='store_true', help='Whether to use adadelta (default is rmsprop)',
                    default=False)
parser.add_argument('--keep_ratio', action='store_true', help='whether to keep ratio for image resize', default=True)
parser.add_argument('--random_sample', action='store_true', help='whether to sample the dataset with random sampler',
                    default=True)
opt = parser.parse_args()
print(opt)

if opt.experiment is None:
    opt.experiment = 'expr1_1'
os.system('mkdir {0}'.format(opt.experiment))

opt.manualSeed = random.randint(1, 10000)  # fix seed
print("Random Seed: ", opt.manualSeed)
random.seed(opt.manualSeed)
np.random.seed(opt.manualSeed)
torch.manual_seed(opt.manualSeed)

cudnn.benchmark = True

if torch.cuda.is_available() and not opt.cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")

train_dataset = dataset.lmdbDataset(root=opt.trainroot)
assert train_dataset
if opt.random_sample:
    sampler = dataset.randomSequentialSampler(train_dataset, opt.batchSize)
else:
    sampler = None
train_loader = torch.utils.data.DataLoader(
    train_dataset, batch_size=opt.batchSize,
    shuffle=False, sampler=sampler,
    num_workers=int(opt.workers),
    collate_fn=dataset.alignCollate(imgH=opt.imgH, imgW=opt.imgW, keep_ratio=opt.keep_ratio))
# test_dataset = dataset.lmdbDataset(
#     root=opt.valroot, transform=dataset.resizeNormalize((opt.imgW, opt.imgH)))  # ???????????

val_dataset = dataset.lmdbDataset(root=opt.valroot)

alphabet = opt.alphabet  # .decode('utf-8')

nclass = len(alphabet) + 1
nc = 1

converter = utils.strLabelConverter(alphabet)
criterion = CTCLoss()


# custom weights initialization called on crnn
def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)

crnn = crnn.CRNN(opt.imgH, nc, nclass, opt.nh)
crnn.apply(weights_init)
if opt.crnn != '':
    print('loading pretrained model from %s' % opt.crnn)
    pre_trainmodel = torch.load(opt.crnn)
    model_dict = crnn.state_dict()
    weig1 = 'module.rnn.1.embedding.weight'
    bias1 = 'module.rnn.1.embedding.bias'
    mymodel = {}
    if len(model_dict[weig1[7:]]) == len(pre_trainmodel[weig1]) and len(model_dict[bias1[7:]]) == len(pre_trainmodel[bias1]):
        for k, v in pre_trainmodel.items():
            mymodel[k[7:]] = v
            # print(k, len(v))
        crnn.load_state_dict(mymodel)
    else:
        for k, v in model_dict.items():
            if (k != weig1 or k != bias1):
                model_dict[k] = pre_trainmodel[k]
        crnn.load_state_dict(model_dict)
print(crnn)

image = torch.FloatTensor(opt.batchSize, 3, opt.imgH, opt.imgH)
text = torch.IntTensor(opt.batchSize * 5)
length = torch.IntTensor(opt.batchSize)

if opt.cuda:
    crnn.cuda()
    crnn = torch.nn.DataParallel(crnn, device_ids=range(opt.ngpu))
    image = image.cuda()
    criterion = criterion.cuda()

image = Variable(image)
text = Variable(text)
length = Variable(length)

# loss averager
loss_avg = utils.averager()

# setup optimizer
if opt.adam:
    optimizer = optim.Adam(crnn.parameters(), lr=opt.lr,
                           betas=(opt.beta1, 0.999))
elif opt.adadelta:
    optimizer = optim.Adadelta(crnn.parameters(), lr=opt.lr)
else:
    optimizer = optim.RMSprop(crnn.parameters(), lr=opt.lr)


def val(net, val_dataset, criterion, max_iter=100):
    print('Start val')

    for p in crnn.parameters():
        p.requires_grad = False

    net.eval()
    data_loader = torch.utils.data.DataLoader(
        val_dataset,shuffle=False, batch_size=opt.batchSize, num_workers=int(opt.workers),collate_fn=dataset.alignCollate(imgH=opt.imgH, imgW=opt.imgW, keep_ratio=opt.keep_ratio))
    val_iter = iter(data_loader)

    i = 0
    n_correct = 0
    loss_avg = utils.averager()

    # max_iter = max(max_iter, len(data_loader))
    max_iter=len(data_loader)
    for i in range(max_iter):
        data = val_iter.next()
        i += 1
        cpu_images, cpu_texts = data
        batch_size = cpu_images.size(0)
        utils.loadData(image, cpu_images)
        t, l = converter.encode(cpu_texts)
        utils.loadData(text, t)
        utils.loadData(length, l)

        preds = crnn(image)
        preds_size = Variable(torch.IntTensor([preds.size(0)] * batch_size))
        cost = criterion(preds, text, preds_size, length) / batch_size
        loss_avg.add(cost)

        _, preds = preds.max(2)
        # preds = preds.squeeze(2)
        preds = preds.transpose(1, 0).contiguous().view(-1)
        sim_preds = converter.decode(preds.data, preds_size.data, raw=False)
        for pred, target in zip(sim_preds, cpu_texts):
            if pred == target:
                n_correct += 1

    raw_preds = converter.decode(preds.data, preds_size.data, raw=True)[:opt.n_test_disp]
    for raw_pred, pred, gt in zip(raw_preds, sim_preds, cpu_texts):
        print('%-20s => %-20s, gt: %-20s' % (raw_pred, pred, gt))

    accuracy = n_correct / float(max_iter * opt.batchSize)
    print('Test loss: %f, accuray: %f' % (loss_avg.val(), accuracy))


def trainBatch(net, criterion, optimizer):
    data = train_iter.next()
    cpu_images, cpu_texts = data
    batch_size = cpu_images.size(0)
    utils.loadData(image, cpu_images)
    t, l = converter.encode(cpu_texts)
    utils.loadData(text, t)
    utils.loadData(length, l)

    preds = crnn(image)

    # dot=make_dot(preds, params=dict(crnn.named_parameters()))

    preds_size = Variable(torch.IntTensor([preds.size(0)] * batch_size))
    cost = criterion(preds, text, preds_size, length) / batch_size
    # if (np.isnan(cost.data.numpy())):
    #     print(net._modules['module'].cnn.conv0.weight.grad)
    #     # print("cost-------------------------------------------------------",cost)
    #     # return

    crnn.zero_grad()
    cost.backward()
    # print(crnn.state_dict())
    optimizer.step()
    return cost


for epoch in range(opt.niter):
    train_iter = iter(train_loader)
    iter_num=len(train_loader)
    i = 0
    while i < iter_num:
        for p in crnn.parameters():
            p.requires_grad = True
        crnn.train()

        cost = trainBatch(crnn, criterion, optimizer)

        loss_avg.add(cost)
        i += 1
        # print(i)
        if i % opt.displayInterval == 0:
            print('[%d/%d][%d/%d] Loss: %f' %
                  (epoch, opt.niter, i, len(train_loader), loss_avg.val()))
            loss_avg.reset()
    if epoch % opt.valInterval == 0:
        val(crnn, val_dataset, criterion)

        # do checkpointing
    if (epoch+1) % opt.saveInterval == 0:
        torch.save(
            crnn.state_dict(), '{0}/netCRNN_{1}_{2}.pth'.format(opt.experiment, epoch, i))
