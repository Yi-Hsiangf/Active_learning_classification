#!/bin/bash
#SBATCH --job-name=ENS_Entropy
#SBATCH --nodes=1
#SBATCH --gres=gpu:1,VRAM:12G
#SBATCH --cpus-per-task=3
#SBATCH --mem=24G
#SBATCH --time=30:00:00
#SBATCH --output=ENS_result/Entropy_cifar100-%j
#SBATCH --exclude=node2

source activate alenv
python3 ../main.py --func Entropy --dataset cifar100 --method ENS
