from data import *
from utils.augmentations import SSDAugmentation
from layers.modules import MultiBoxLoss
from ssd import build_ssd
from os.path import join
import os
import sys
import time
import torch
from pathlib import Path
from torch.autograd import Variable
import torch.nn as nn
import torch.optim as optim
import torch.backends.cudnn as cudnn
import torch.nn.init as init
import torch.utils.data as data
import torchvision

import numpy as np
import argparse
import tqdm

sys.path.append(os.path.abspath('../../active_learning'))
from active_loss import LossPredictionLoss
from active_learning_utils import *
from active_learning import ActiveLearning



def str2bool(v):
    return v.lower() in ("yes", "true", "t", "1")


def create_dir_if_doesnt_exist(path):
    # Create working dir if not exists.
    directory=Path(path)
    if not directory.exists():
        directory.mkdir(parents=True)


parser = argparse.ArgumentParser(
    description='Single Shot MultiBox Detector Training With Pytorch'
)
train_set = parser.add_mutually_exclusive_group()
parser.add_argument(
    '--dataset',
    default='VOC',
    choices=['VOC', 'COCO'],
    type=str,
    help='VOC or COCO'
)
parser.add_argument(
    '--dataset_root', default=VOC_ROOT, help='Dataset root directory path'
)

parser.add_argument(
    '--basenet', default='vgg16_reducedfc.pth', help='Pretrained base model'
)
parser.add_argument(
    '--batch_size', default=32, type=int, help='Batch size for training'
)
parser.add_argument(
    '--resume',
    default=None,
    type=str,
    help='Checkpoint state_dict file to resume training from'
)
parser.add_argument(
    '--start_iter', default=0, type=int, help='Resume training at this iter'
)
parser.add_argument(
    '--num_workers',
    default=4,
    type=int,
    help='Number of workers used in dataloading'
)
parser.add_argument(
    '--cuda', default=True, type=str2bool, help='Use CUDA to train model'
)
parser.add_argument(
    '--lr',
    '--learning-rate',
    default=1e-3,
    type=float,
    help='initial learning rate'
)
parser.add_argument(
    '--momentum', default=0.9, type=float, help='Momentum value for optim'
)
parser.add_argument(
    '--weight_decay', default=5e-4, type=float, help='Weight decay for SGD'
)
parser.add_argument(
    '--gamma', default=0.1, type=float, help='Gamma update for SGD'
)


parser.add_argument(
    '--Acq_func', type=str, default='LLAL', help='Choose Acquisition Function'
)


parser.add_argument(
    '--lamda', default=1, type=int, help='Active learning loss weight'
)

parser.add_argument(
    '--visdom',
    default=False,
    type=str2bool,
    help='Use visdom for loss visualization'
)
parser.add_argument(
    '--save_folder',
    default='weights/',
    help='Directory for saving checkpoint models'
)
parser.add_argument(
    '--output_superannotate_csv_file',
    required=False,
    type=str,
    default=None,
    help='Path to the output csv file with the selected indices. Can be uploaded to annotate.online.')

args = parser.parse_args()
COCO_ROOT=''
if torch.cuda.is_available():
    if args.cuda:
        torch.set_default_tensor_type('torch.cuda.FloatTensor')
    if not args.cuda:
        print(
            "WARNING: It looks like you have a CUDA device, but aren't " +
            "using CUDA.\nRun with --cuda for optimal training speed."
        )
        torch.set_default_tensor_type('torch.FloatTensor')
else:
    torch.set_default_tensor_type('torch.FloatTensor')

if not os.path.exists(args.save_folder):
    os.mkdir(args.save_folder)


def train():
    if args.dataset == 'COCO':
        if args.dataset_root == VOC_ROOT:
            if not os.path.exists(COCO_ROOT):
                parser.error('Must specify dataset_root if specifying dataset')
            print(
                "WARNING: Using default COCO dataset_root because " +
                "--dataset_root was not specified."
            )
            args.dataset_root = COCO_ROOT
        cfg = coco
        dataset = COCODetection(
            root=args.dataset_root,
            transform=SSDAugmentation(cfg['min_dim'], MEANS)
        )
    elif args.dataset == 'VOC':
        if args.dataset_root == COCO_ROOT:
            parser.error('Must specify dataset if specifying dataset_root')
        cfg = voc
        dataset = VOCDetection(
            root=args.dataset_root,
            transform=SSDAugmentation(cfg['min_dim'], MEANS)
        )
    IMG_CNT = len(dataset)
    if args.visdom:
        import visdom
        viz = visdom.Visdom()

    criterion = MultiBoxLoss(
        cfg['num_classes'], 0.5, True, 0, True, 3, 0.5, False, args.cuda
    )

    # loss counters
    loc_loss = 0
    conf_loss = 0
    epoch = 0
    print('Loading the dataset...')

    rand_state = np.random.RandomState(1311)
    epoch_size = len(dataset) // args.batch_size
    print("len of dataset: ", len(dataset))  
    print("ep size: ", epoch_size)
    global pool_idx
    pool_idx = list(range(IMG_CNT))
    print("pool :",range(IMG_CNT))
    print('Training SSD on:', args.dataset)
    print('Using the specified args:')
    print(args)

    step_index = 0

    if args.visdom:
        vis_title = 'SSD.PyTorch on ' + dataset.name
        vis_legend = ['Loc Loss', 'Conf Loss', 'Total Loss']
        iter_plot = create_vis_plot('Iteration', 'Loss', vis_title, vis_legend)
        epoch_plot = create_vis_plot('Epoch', 'Loss', vis_title, vis_legend)

    #random image choice
    train_idx = []
    progress = tqdm.tqdm(range(1))
    for cycle in progress:
        net = build_ssd('train', cfg['min_dim'], cfg['num_classes'])

        if args.Acq_func == 'LLAL':
            net = ActiveLearning(net)
 
        if args.cuda:
            net = torch.nn.DataParallel(net)
            cudnn.benchmark = True

        if args.resume:
            print('Resuming training, loading {}...'.format(args.resume))
            net.load_weights(args.resume)
        else:
            vgg_weights = torch.load(args.save_folder + args.basenet)
            print('Loading base network...')
            if args.Acq_func == 'LLAL':
                if args.cuda:
                    net.module.base_model.vgg.load_state_dict(vgg_weights)
                else:
                    net.base_model.vgg.load_state_dict(vgg_weights)
            else:
                if args.cuda:
                    net.module.vgg.load_state_dict(vgg_weights)
                else:
                    net.vgg.load_state_dict(vgg_weights)


        if args.cuda:
            net = net.cuda()

        if not args.resume:
            print('Initializing weights...')
            # initialize newly added layers' weights with xavier method
            if args.Acq_func == 'LLAL':
                if args.cuda:
                    model = net.module.base_model
                else:
                   model = net.base_model
            else:
                if args.cuda:
                    model = net.module
                else:
                   model = net
            model.extras.apply(weights_init)
            model.loc.apply(weights_init)
            model.conf.apply(weights_init)

        net.train()

        al_dataset = VOCDetection(
                 root=args.dataset_root,
                 transform=BaseTransform(300, MEANS)
        )
        device = 'cuda' if (torch.cuda.is_available() and args.cuda) else 'cpu'

        if args.Acq_func == 'LLAL' and not cycle == 0:
            indices, losses = choose_indices_loss_prediction_active_learning(
                net, cycle, rand_state, pool_idx, al_dataset, device)
            train_idx.extend(indices)

            if args.output_superannotate_csv_file is not None:
                # Write image paths to csv file which can be uploaded to annotate.online.
                write_entropies_csv(
                    dataset, indices,losses, args.output_superannotate_csv_file)
        else:
            if args.Acq_func == "Random":
                train_idx.extend(random_indices(pool_idx, rand_state, count=1000))
            elif args.Acq_func == "Entropy":
                selected_idx = get_entropy_uncertainty(net, cycle, args.lr, rand_state, pool_idx, al_dataset, device)
                train_idx.extend(selected_idx)
            else:
                train_idx.extend(random_indices(pool_idx, rand_state, count=1000))

        cfg = voc
        dataset = data.Subset(VOCDetection(
            root=args.dataset_root,
            transform=SSDAugmentation(cfg['min_dim'], MEANS)
            ), train_idx
        )
        data_loader = data.DataLoader(
            dataset,
            args.batch_size,
            num_workers=args.num_workers,
            shuffle=True,
            collate_fn=detection_collate,
            pin_memory=True
        )
        optimizer = optim.SGD(
            net.parameters(),
            lr=args.lr,
            momentum=args.momentum,
            weight_decay=args.weight_decay
        )
        

        for epoch in range(300):
            if epoch == 240:
                adjust_learning_rate(optimizer)
            for iteration, (images, targets) in enumerate(data_loader):
                if args.cuda:
                    images = Variable(images.cuda())
                    targets = [Variable(ann.cuda(), requires_grad=False) for ann in targets]
                else:
                    images = Variable(images)
                    targets = [Variable(ann, requires_grad=False) for ann in targets]
                # forward
                t0 = time.time()
                if args.Acq_func == "LLAL":
                    out, loss_pred = net(images)

                else:
                    out =  net(images)
                # backprop
                
                optimizer.zero_grad()
                loss_l, loss_c, N = criterion(out, targets)
                # print("loss_l {} loss_c {} N {}".format(loss_l, loss_c, N))
                loss = (loss_l + loss_c).sum() / N
                # print("loss = {}".format(loss))
                if args.Acq_func == "LLAL":
                    criterion_lp = LossPredictionLoss()
                    loss_prediction_loss = criterion_lp(loss_pred, loss_l + loss_c)
                    loss += args.lamda * loss_prediction_loss
                loss.backward()
                optimizer.step()
                t1 = time.time()
                loc_loss += loss_l.mean().item()
                conf_loss += loss_c.mean().item()

                if iteration % 10 == 0:
                    progress.set_description('timer: %.4f sec, ' % (t1 - t0) + 
                        'iter ' + repr(iteration) + (', Loss: %.4f' %
                        (loss.item())))
                        
                        
        if args.Acq_func == "LLAL":
            folder = join(args.save_folder, 'LLAL')
        elif args.Acq_func == "Random":
            folder = join(args.save_folder, 'Random')
        elif args.Acq_func == "Entropy":
            folder = join(args.save_folder, 'Entropy')  

        
        
        create_dir_if_doesnt_exist(folder)
        torch.save(
            net.state_dict(),
            join(folder, 'lr_' + str(args.lr) + '_cycle_'+str(cycle+1)+'k.pth')
        )

def adjust_learning_rate(optimizer):
    """Sets the learning rate to the initial LR decayed by 10 at every
        specified step
    # Adapted from PyTorch Imagenet example:
    # https://github.com/pytorch/examples/blob/master/imagenet/main.py
    """
    lr = args.lr * 0.1
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


def xavier(param):
    init.xavier_uniform_(param)


def weights_init(m):
    if isinstance(m, nn.Conv2d):
        xavier(m.weight.data)
        m.bias.data.zero_()


def create_vis_plot(_xlabel, _ylabel, _title, _legend):
    return viz.line(
        X=torch.zeros((1, )).cpu(),
        Y=torch.zeros((1, 3)).cpu(),
        opts=dict(xlabel=_xlabel, ylabel=_ylabel, title=_title, legend=_legend)
    )


def update_vis_plot(
    iteration, loc, conf, window1, window2, update_type, epoch_size=1
):
    viz.line(
        X=torch.ones((1, 3)).cpu() * iteration,
        Y=torch.Tensor([loc, conf, loc + conf]).unsqueeze(0).cpu() / epoch_size,
        win=window1,
        update=update_type
    )
    # initialize epoch plot on first iteration
    if iteration == 0:
        viz.line(
            X=torch.zeros((1, 3)).cpu(),
            Y=torch.Tensor([loc, conf, loc + conf]).unsqueeze(0).cpu(),
            win=window2,
            update=True
        )


if __name__ == '__main__':
    start = time.time()
    train()
    end = time.time()
    print("total time: ", end - start)
