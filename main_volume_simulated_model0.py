import numpy as np
import torch
import torch.nn as nn
import matplotlib.pyplot as plt
plt.ion()
# from skimage.transform import resize
import mrcfile
import time
# from torchsummary import summary
# from ops.radon_3d_lib import ParallelBeamGeometry3DOpAngles, ParallelBeamGeometry3DOpAngles_rectangular
import os
# import imageio
import torch.nn.functional as F
import torch.nn as nn

# from utils import data_generation, reconstruction, utils_deformation, utils_sampling, utils_interpolation, utils_display, utils_ricardo
from utils import utils_deformation, utils_ricardo
# from utils.reconstruction import getfsc

from torch.utils.data import DataLoader, TensorDataset
# from utils.utils_sampling import sample_implicit, generate_rays_batch, generate_ray 
from utils.utils_sampling import sample_implicit_batch_lowComp, generate_rays_batch_bilinear


# import json

from configs.config_reconstruct_simulation import get_default_configs
config = get_default_configs()


import warnings
warnings.filterwarnings('ignore') 

# Introduction
'''
This script is used to generate data for AreTomo. The final goal is to use the same data generated by this script with our
method to compare with AreTomo.
'''

use_cuda=torch.cuda.is_available()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu") 
if torch.cuda.device_count()>1:
    torch.cuda.set_device(config.device_num)
np.random.seed(config.seed)
torch.manual_seed(config.seed)


# ## TODO: load data from config file
# config.volume_name = 'model_0'

# # Parameters for the data generation
# # size of the volume to use to generate the tilt-series
# config.n1 = 512
# config.n2 = 512
# n3 = 180 # size of the effective volume
# # size of the patch to crop in the raw volume
# config.n1_patch = 512
# config.n2_patch = 512
# n3_patch = 180 # size of the effective volume
# # nZ = 512 # size of the extended volume
# config.Nangles = 61
# view_angle_min = -60
# view_angle_max = 60
# SNR_value = 10
# config.sigma_PSF = 3.
# number_sub_projections = 1

# scale_min = 1.0
# scale_max = 1.0
# shift_min = -0.04
# shift_max = 0.04
# shear_min = -0.0
# shear_max = 0.0
# angle_min = -4/180*np.pi
# angle_max = 4/180*np.pi
# sigma_local_def = 4
# N_ctrl_pts_local_def = (12,12)


# # Parameters for the data generation
# config.path_load = "./results/"+str(config.volume_name)+"_SNR_"+str(SNR_value)+"_size_"+str(config.n1)+"_no_PSF/"
# config.path_save = "./results/model0_SNR_"+str(10)+"_size_"+str(512)+"_no_PSF/"

## TODO: use folloying to check that the load data are consistent with the current parameters
# params = np.load(config.path_load+"parameters.npz")
# config.n1 = params['config.n1'].item()
# config.n2 = params['config.n2'].item()
# n3 = params['n3'].item()
# SNR_value = params['SNR_value'].item()
# config.sigma_PSF = params['config.sigma_PSF'].item()
# angle_bound = params['angle_bound'].item()
# config.Nangles = params['config.Nangles'].item()

# grid_class = utils_sampling.grid_class(config.n1,config.n2,n3,config.torch_type,device)
# us = 4 # under sample factor
# grid_class_us = utils_sampling.grid_class(config.n1//us,config.n2//us,n3//us,config.torch_type,device)


# config.path_save = "./results/model0_SNR_"+str(SNR_value)+"_size_"+str(config.n1)+"/"
if not os.path.exists("results/"):
    os.makedirs("results/")
if not os.path.exists(config.path_save):
    os.makedirs(config.path_save)
if not os.path.exists(config.path_save+"projections/"):
    os.makedirs(config.path_save+"projections/")
if not os.path.exists(config.path_save+"grid/"):
    os.makedirs(config.path_save+"grid/")
if not os.path.exists(config.path_save+"deformations/"):
    os.makedirs(config.path_save+"deformations/")
if not os.path.exists(config.path_save+"deformations/training/"):
    os.makedirs(config.path_save+"deformations/training/")
if not os.path.exists(config.path_save+"projections/training/"):
    os.makedirs(config.path_save+"projections/training/")
if not os.path.exists(config.path_save+"projections/volume/"):
    os.makedirs(config.path_save+"projections/volume/")
if not os.path.exists(config.path_save+"training/"):
    os.makedirs(config.path_save+"training/")

## TODO: do we need all the following for training?
data = np.load(config.path_load+"volume_and_projections.npz")
projections_noisy = torch.tensor(data['projections_noisy']).type(config.torch_type).to(device)
projections_deformed = torch.tensor(data['projections_deformed']).type(config.torch_type).to(device)
projections_deformed_global = torch.tensor(data['projections_deformed_global']).type(config.torch_type).to(device)
projections_clean = torch.tensor(data['projections_clean']).type(config.torch_type).to(device)
PSF = torch.tensor(data['PSF']).type(config.torch_type).to(device)
if config.sigma_PSF!=0:
    supp_PSF = max(PSF.shape)
    xx1 = np.linspace(-config.n1//2,config.n1//2,config.n1)
    xx2 = np.linspace(-config.n2//2,config.n2//2,config.n2)
    XX, YY = np.meshgrid(xx1,xx2)
    G = np.exp(-(XX**2+YY**2)/(2*config.sigma_PSF**2))
    supp = int(np.round(4*config.sigma_PSF))
    PSF = G[config.n1//2-supp//2:config.n1//2+supp//2,config.n2//2-supp//2:config.n2//2+supp//2]
    PSF /= PSF.sum()
else: 
    PSF = 0



affine_tr = np.load(config.path_load+"global_deformations.npy",allow_pickle=True)
local_tr = np.load(config.path_load+"local_deformations.npy", allow_pickle=True)

V_t = torch.tensor(np.moveaxis(np.double(mrcfile.open(config.path_load+"V.mrc").data),0,2)).type(config.torch_type).to(device)
V_FBP_no_deformed_t = torch.tensor(np.moveaxis(np.double(mrcfile.open(config.path_load+"V_FBP_no_deformed.mrc").data),0,2)).type(config.torch_type).to(device)
V_FBP =  torch.tensor(np.moveaxis(np.double(mrcfile.open(config.path_load+"V_FBP.mrc").data),0,2)).type(config.torch_type).to(device)

V_ = V_t.detach().cpu().numpy()
V_FBP_ = V_FBP.detach().cpu().numpy()
V_FBP_no_deformed = V_FBP_no_deformed_t.detach().cpu().numpy()

V_ /= V_.sum()
V_FBP_ /= V_FBP_.sum()
V_FBP_no_deformed /= V_FBP_no_deformed.sum()
fsc_FBP = utils_ricardo.FSC(V_,V_FBP_)
fsc_FBP_no_deformed = utils_ricardo.FSC(V_,V_FBP_no_deformed)
x_fsc = np.arange(fsc_FBP.shape[0])


######################################################################################################
######################################################################################################
##
## TRAINING
##
######################################################################################################
######################################################################################################

# # TODO: make this parameter inside a config file
# # Estimate Volume from the deformed projections
# train_volume = True
# train_local_def = True
# train_global_def = True
# # train_all = True # train or load model
# # learn_volume = True # learn the volume
# # learn_global = True # learn global dn3/max(config.n1,config.n2)/np.cos((90-angle_bound)*np.pi/180)eformation
# # learn_local = True # learn local deformation
# local_model = 'interp' #  'implicit' or 'interp'
# initialize_local_def = False
# initialize_volume = False
# # use_deformation_estimation = True # estimate deformation, useful to see what happens when we don't
# volume_model = "Fourier-features" # multi-resolution, Fourier-features, grid, MLP
# # model_type = 2 #0 for Fourier feature, 1 for MLP

# # When to start or stop optimizing over a variable
# schedule_local = []
# schedule_global = []
# schedule_volume = []

# batch_size = 10 # number of viewing direction per iteration
# config.nRays =  1500 # number of sampling rays per viewing direction
# # ray_length = 512 # number of points along one ray
# # TODO: try to change that
# z_max = 2*n3/max(config.n1,config.n2)/np.cos((90-np.max([angle_min,angle_max]))*np.pi/180)
# ray_length = 500#int(np.floor(config.n1*z_max))
# # TODO: try to chnage that with z_max
# config.rays_scaling = [1.,1.,1.] # scaling of the coordinatesalong each axis. To make sure that the input of implicit net stay in their range

# ## Parameters
# epochs = 400
# Ntest = 25 # number of epoch before display
# NsaveNet = 100 # number of epoch before saving again the nets
# # iter_local = 1000000 # include training of local deformations after few epochs
# # frac = 1
# lr_volume = 1e-2
# # lr_global_def =1e-4
# lr_local_def = 1e-4
# lr_shift = 1e-3
# lr_rot = 1e-3

# lamb_volume = 0*1e-5 # regul parameters on volume regularization
# lamb_volume_out = 0*1e-0 # regul parameters on volume regularization to be 0 outside domain
# lamb_local = 0*1e-3 # regul parameters on local deformation
# # lamb_local_smooth = 0*1e-8 # regul parameters on local deformation to be smooth
# lamb_local_ampl = 1e2 # regul on amplitude of local def.
# lamb_rot = 1e-6 # regul parameters on inplane rotations
# lamb_shifts = 1e-6 # regul parameters on shifts
# wd = 5e-6 # weights decay
# # config.Nangles_ = config.Nangles
# scheduler_step_size = 200
# scheduler_gamma = 0.6
# delay_deformations = 25 # Delay before learning deformations

# # Params of implicit deformation
# deformationScale = 1
# inputRange = 1
# Npts_rd = 500 # number of random points to compute regul

# # if implicit model
# input_size = 2
# output_size = 2
# num_layers = 3
# hidden_size = 32
# L = 10
# # if interpolation model
# N_ctrl_pts_net = 20

# # params of implicit volume
# config.input_size_volume = 3
# output_size_volume = 1
# num_layers_volume = 4
# hidden_size_volume = 128
# L_volume = 3

# mollifier = utils_sampling.mollifier_class(-1,config.torch_type,device)
# barycenter_true = torch.zeros(3).type(config.torch_type).to(device)

######################################################################################################
## Define the volume
# from models.fourier_net import FourierNet
# impl_volume = FourierNet(
#     in_features=config.input_size_volume,
#     out_features=output_size_volume,
#     hidden_features=hidden_size_volume,
#     hidden_blocks=num_layers_volume,
#     L = L_volume).to(device)  
# num_param = sum(p.numel() for p in impl_volume.parameters() if p.requires_grad) 
# print('---> Number of trainable parameters in volume net: {}'.format(num_param))


# from models.fourier_net import MultiResImplicitRepresentation
# nFeature_volume = 32
# res_volume = (8,16,32,64)
# impl_volume = MultiResImplicitRepresentation(d=3, nFeature=nFeature_volume, res=res_volume, L=L,
#                                       hidden_features=hidden_size_volume,
#                                        hidden_blocks=num_layers_volume,
#                                         out_features=1).cuda()

# num_param = sum(p.numel() for p in impl_volume.parameters() if p.requires_grad) 
# print('---> Number of trainable parameters in volume net: {}'.format(num_param))

# Some processing
if config.sigma_PSF!=0:
    config.nRays = config.nRays//(supp_PSF**2)
    psf_shift = torch.zeros((supp_PSF,supp_PSF)).type(config.torch_type).to(device)
    xx_ = torch.tensor(np.arange(-supp_PSF//2,supp_PSF//2)/config.n1).type(config.torch_type).to(device)
    yy_ = torch.tensor(np.arange(-supp_PSF//2,supp_PSF//2)/config.n2).type(config.torch_type).to(device)
    psf_shift_x,psf_shift_y = torch.meshgrid(xx_,yy_)
    psf_shift_x = psf_shift_x.reshape(1,1,-1,1)
    psf_shift_y = psf_shift_y.reshape(1,1,-1,1)
config.rays_scaling = torch.tensor(config.rays_scaling)[None,None,None]


## Volume Network
# # TODO: define grid network
# if config.volume_name == "multi-resolution":
#     config = {"encoding": {
#             'otype': 'Grid',
#             'type': 'Hash',
#             'n_levels': 8,
#             'n_features_per_level': 2,
#             'log2_hashmap_size': 24,
#             'base_resolution': 4,
#             'per_level_scale': 2,
#             'interpolation': 'Smoothstep'
#         }}

#     try: 
#         grid = tcnn.Encoding(3,encoding_config=config['encoding'],dtype=torch.float32).to(device)
#         sub_features = grid.n_output_dims
#         # print(sub_features)
#     except:
#         print("Error")

#     grid = tcnn.Encoding(3,encoding_config=config['encoding'],dtype=torch.float32).to(device)
#     sub_features = grid.n_output_dims
# print(sub_features)
if(config.volume_model=="Fourier-features"):
    from models.fourier_net import FourierNet,FourierNet_Features
    impl_volume = FourierNet_Features(
        in_features=config.input_size_volume,
        sub_features=config.sub_features,
        out_features=config.output_size_volume, 
        hidden_features=config.hidden_size_volume,
        hidden_blocks=config.num_layers_volume,
        L = config.L_volume).to(device)

    # class VolumeNet(torch.nn.Module):
    #     def __init__(self,grid, impl_volume):
    #         super(VolumeNet, self).__init__()
    #         self.grid = grid
    #         self.impl_volume = impl_volume
    #     def forward(self, xs):
    #         xx = self.grid(xs)
    #         #print(xx.shape)
    #         xs = torch.cat((xs, xx), dim=1)
    #         #print(xs.shape)
    #         return self.impl_volume(xs)
    # impl_volume = VolumeNet(config.grid, impl_volume_net).to(device)
        
if(config.volume_model=="MLP"):
    from models.fourier_net import MLP
    impl_volume = MLP(in_features= 1, 
                          hidden_features=config.hidden_size_volume, hidden_blocks= config.num_layers_volume, out_features=config.output_size_volume).to(device)
    # class VolumeNet(torch.nn.Module):
    #     def __init__(self,grid, impl_volume):
    #         super(VolumeNet, self).__init__()
    #         self.grid = grid
    #         self.impl_volume = impl_volume
    #     def forward(self, xs):
    #         gridOp = self.grid(xs)
    #         return self.impl_volume(gridOp) 
    # impl_volume = VolumeNet(grid, impl_volume_net).to(device)

if(config.volume_model=="multi-resolution"):  
    import tinycudann as tcnn
    config = {"encoding": {
            'otype': 'Grid',
            'type': 'Hash',
            'n_levels': 9,
            'n_features_per_level': 2,
            'log2_hashmap_size': 20,
            'base_resolution': 8,
            'per_level_scale': 2,
            'interpolation': 'Smoothstep'
        },
        "network": {
            "otype": "FullyFusedMLP",   
            "activation": "ReLU",       
            "output_activation": "None",
            "n_neurons": config.hidden_size_volume,           
            "n_hidden_layers": config.num_layers_volume,       
        }       
        }
    impl_volume = tcnn.NetworkWithInputEncoding(n_input_dims=3, n_output_dims=1, encoding_config=config["encoding"], network_config=config["network"]).to(device)

num_param = sum(p.numel() for p in impl_volume.parameters() if p.requires_grad) 
print('---> Number of trainable parameters in volume net: {}'.format(num_param))


# TODO: add Gaussian blob with trainable position and directions
######################################################################################################
## Define the implicit deformations
if config.local_model=='implicit':
    # Define Implicit representation of local deformations
    implicit_deformation_list = []
    for k in range(config.Nangles):
        implicit_deformation = FourierNet(
            in_features=config.input_size,
            out_features=config.output_size,
            hidden_features=config.hidden_size,
            hidden_blocks=config.num_layers,
            L = config.L).to(device)
        implicit_deformation_list.append(implicit_deformation)

    num_param = sum(p.numel() for p in implicit_deformation_list[0].parameters() if p.requires_grad) 
    print('---> Number of trainable parameters in implicit net: {}'.format(num_param))
if config.local_model=='interp':
    depl_ctr_pts_net = torch.zeros((2,config.N_ctrl_pts_net,config.N_ctrl_pts_net)).to(device).type(config.torch_type)/max([config.n1,config.n2,n3])/10
    implicit_deformation_list = []
    for k in range(config.Nangles):
        # depl_ctr_pts_net = local_tr[k].depl_ctr_pts.clone().detach()[0].cuda()/deformationScale
        field = utils_deformation.deformation_field(depl_ctr_pts_net.clone(),maskBoundary=2)
        implicit_deformation_list.append(field)
    num_param = sum(p.numel() for p in implicit_deformation_list[0].parameters() if p.requires_grad) 
    print('---> Number of trainable parameters in implicit net: {}'.format(num_param))


######################################################################################################
## Define the global deformations
shift_est = []
rot_est = []
for k in range(config.Nangles):
    shift_est.append(utils_deformation.shiftNet(1).to(device))
    rot_est.append(utils_deformation.rotNet(1).to(device))


######################################################################################################
# Optimizer
loss_data = config.loss_data

train_global_def = config.train_global_def
train_local_def = config.train_local_def
# list_params = []
# list_params_with_local = []
list_params_deformations_glob = []
list_params_deformations_loc = []
if(train_global_def or train_local_def):
    for k in range(config.Nangles):
        if train_global_def:
            list_params_deformations_glob.append({"params": shift_est[k].parameters(), "lr": config.lr_shift})
            list_params_deformations_glob.append({"params": rot_est[k].parameters(), "lr": config.lr_rot})
        if train_global_def:
            list_params_deformations_loc.append({"params": implicit_deformation_list[k].parameters(), "lr": config.lr_local_def})

# learn_deformations = False
optimizer_volume = torch.optim.Adam(impl_volume.parameters(), lr=config.lr_volume, weight_decay=config.wd)
optimizer_deformations_glob = torch.optim.Adam(list_params_deformations_glob, weight_decay=config.wd)
optimizer_deformations_loc = torch.optim.Adam(list_params_deformations_loc, weight_decay=config.wd)

scheduler_volume = torch.optim.lr_scheduler.StepLR(optimizer_volume, step_size=config.scheduler_step_size, gamma=config.scheduler_gamma)
if train_global_def:
    scheduler_deformation_glob = torch.optim.lr_scheduler.StepLR(
        optimizer_deformations_glob, step_size=config.scheduler_step_size, gamma=config.scheduler_gamma)
if train_local_def:
    scheduler_deformation_loc = torch.optim.lr_scheduler.StepLR(
        optimizer_deformations_loc, step_size=config.scheduler_step_size, gamma=config.scheduler_gamma)

######################################################################################################
# Format data for batch training
index = torch.arange(0, config.Nangles, dtype=torch.long) # index for the dataloader

# Define dataset
angles = np.linspace(config.angle_min,config.angle_max,config.Nangles)
angles_t = torch.tensor(angles).type(config.torch_type).to(device)
dataset = TensorDataset(angles_t,projections_noisy.detach(),index)
trainLoader = DataLoader(dataset, batch_size=config.batch_size, shuffle=True, drop_last=True)


# TODO: remove or keep this part?
######################################################################################################
## Track sampling
choosenLocations_all = {}
for a in angles:
    choosenLocations_all[a] = []

current_sampling = np.ones_like(projections_noisy.detach().cpu().numpy())

# x = np.arange(0,config.n1)
# y = np.arange(0,config.n2)
# X, Y = np.meshgrid(x, y)
# loc_grid = torch.Tensor(np.stack([X,Y],axis = 2).reshape(-1,2)).long().to(device)

# loc_grid_3D = loc_grid_3D = grid_class.grid3d_t
# volLoader = DataLoader(loc_grid_3D, batch_size=10000, shuffle=False)


######################################################################################################
## Iterative optimization
loss_tot = []
loss_data_fidelity = []
loss_regul_local_smooth = []
loss_regul_local_ampl = []
loss_regul_volume = []
loss_regul_shifts = []
loss_regul_rot = []
SNR_tot = []
t_test = []

use_local_def = True if train_local_def else False
use_global_def = True if train_global_def else False
t0 = time.time()
for ep in range(config.epochs):
    if(ep>config.delay_deformations):
        learn_deformations = True
    # if ep%int(0.2*epochs)==0 and ep!=0:
    #     frac += 0.2
    #     print("###################")
    #     print("######  Now use {}% of the feature encoding".format(int(np.round(frac*100,0))))
    for   angle,proj, idx_loader  in trainLoader:
        optimizer_volume.zero_grad()
        if learn_deformations:
            optimizer_deformations_glob.zero_grad()
            optimizer_deformations_loc.zero_grad()

            # Check if we stop or start learning something new
            if ep in config.schedule_local:
                if train_local_def:
                    train_local_def = False
                else:
                    train_local_def = True
                if use_local_def is False:
                    use_local_def = True
            if ep in config.schedule_global:
                if train_global_def:
                    train_global_def = False
                else:
                    train_global_def = True
                if use_global_def is False:
                    use_global_def = True
            if ep in config.schedule_volume:
                if train_volume:
                    train_volume = False
                else:
                    train_volume = True


        # Choosing the subset of the parameters
        if(use_local_def):
            local_deformSet= list(map(implicit_deformation_list.__getitem__, idx_loader))
        else:
            local_deformSet = None
        if use_global_def:
            rot_deformSet= list(map(rot_est.__getitem__, idx_loader))
            shift_deformSet= list(map(shift_est.__getitem__, idx_loader))
        else:
            rot_deformSet = None
            shift_deformSet = None

        ## Sample the rays
        ## TODO: make sure that every parameter can be changed in config file
        ## TODO: add an option for density_sampling
        raysSet,raysRot, isOutsideSet, pixelValues = generate_rays_batch_bilinear(proj,angle,config.nRays,config.ray_length,
                                                                                            randomZ=2,zmax=config.z_max,
                                                                                            choosenLocations_all=choosenLocations_all,density_sampling=None)

        if config.sigma_PSF!=0:
            raysSet_ = raysSet.reshape(config.batch_size,config.nRays,1,config.ray_length,3).repeat(1,1,supp_PSF**2,1,1)
            raysSet_[:,:,:,:,0] = raysSet_[:,:,:,:,0]+psf_shift_x
            raysSet_[:,:,:,:,1] = raysSet_[:,:,:,:,1]+psf_shift_y
            raysSet = raysSet_.reshape(config.batch_size,config.nRays*supp_PSF**2,config.ray_length,3)

        raysSet = raysSet*config.rays_scaling 
        outputValues,support = sample_implicit_batch_lowComp(impl_volume,raysSet,angle,
            rot_deformSet=rot_deformSet,shift_deformSet=shift_deformSet,local_deformSet=local_deformSet,
            scale=config.deformationScale,range=config.inputRange,zlimit=config.n3/max(config.n1,config.n2))
        outputValues = outputValues.type(config.torch_type)

        if config.sigma_PSF!=0:
            outputValues = (outputValues.reshape(config.batch_size,config.nRays,supp_PSF**2,config.ray_length)*PSF).sum(2)
            support = support.reshape(outputValues.shape[0],outputValues.shape[1],supp_PSF**2,-1)
            support = support[:,:,supp_PSF**2//2,:] # take only the central elements
        else:
            support = support.reshape(outputValues.shape[0],outputValues.shape[1],-1)
            
        # Compute the projections
        projEstimate = torch.sum(support*outputValues,2)/config.n3

        # Take the datafidelity loss
        loss = loss_data(projEstimate,pixelValues.to(projEstimate.dtype))
        loss_data_fidelity.append(loss.item())

        # update sampling
        with torch.no_grad():
            for jj, ii_ in enumerate(idx_loader):
                ii = ii_.item()
                idx = np.floor((choosenLocations_all[angles[ii]][-1]+1)/2*max(config.n1,config.n2)).astype(np.int)
                current_sampling[ii,idx[:,0],idx[:,1]] += 1

        ## Add regularizations
        if train_local_def and config.lamb_local_ampl!=0:
            depl = torch.abs(implicit_deformation_list[ii](raysSet.reshape(-1,3)))
            loss += config.lamb_local_ampl*(depl.mean())
            loss_regul_local_ampl.append(config.lamb_local_ampl*depl.mean().item())
        if train_global_def and (config.lamb_rot!=0 or config.lamb_shifts!=0):
            for ii in idx_loader:
                loss += config.lamb_shifts*torch.abs(shift_est[ii]()).sum()
                loss += config.lamb_rot*torch.abs(rot_est[ii]()).sum()
                loss_regul_shifts.append((config.lamb_shifts*torch.abs(shift_est[ii]()).sum()).item())
                loss_regul_rot.append((config.lamb_rot*torch.abs(rot_est[ii]()).sum()).item())
        
        if train_volume and config.lamb_volume!=0:
            V_est = impl_volume(raysSet)
            loss += torch.linalg.norm(outputValues[outputValues<0])*config.lamb_volume
            loss_regul_volume.append((torch.linalg.norm(outputValues[outputValues<0])*config.lamb_volume).item())

        loss.backward()
        if train_volume:
            optimizer_volume.step()
        if train_global_def:
            optimizer_deformations_glob.step()
        if train_local_def:
            optimizer_deformations_loc.step()
        loss_tot.append(loss.item())

    scheduler_volume.step()
    scheduler_deformation_glob.step()
    scheduler_deformation_loc.step()

    loss_current_epoch = np.mean(loss_tot[-len(trainLoader):])
    l_fid = np.mean(loss_data_fidelity[-len(trainLoader):])
    l_v = np.mean(loss_regul_volume[-len(trainLoader):])
    l_sh = np.mean(loss_regul_shifts[-len(trainLoader):])
    l_rot = np.mean(loss_regul_rot[-len(trainLoader):])
    l_loc = np.mean(loss_regul_local_ampl[-len(trainLoader):])
    print("Epoch: {}, loss_avg: {:2.3} || Loss data fidelity: {:2.3}, regul volume: {:2.3}, regul shifts: {:2.3}, regul inplane: {:2.3}, regul local: {:2.3}, time: {:2.3}".format(
        ep,loss_current_epoch,l_fid,l_v,l_sh,l_rot,l_loc,time.time()-t0))

    if ep%config.Ntest==0 :#and ep!=0:
        x_lin1 = np.linspace(-1,1,config.n1)*config.rays_scaling[0,0,0,0].item()/2+0.5
        x_lin2 = np.linspace(-1,1,config.n2)*config.rays_scaling[0,0,0,1].item()/2+0.5
        XX, YY = np.meshgrid(x_lin1,x_lin2,indexing='ij')
        grid2d = np.concatenate([XX.reshape(-1,1),YY.reshape(-1,1)],1)
        grid2d_t = torch.tensor(grid2d).type(config.torch_type)
        z_range = np.linspace(-1,1,15)*config.rays_scaling[0,0,0,2].item()*(config.n3/config.n1)/2+0.5
        for zz, zval in enumerate(z_range):
            grid3d = np.concatenate([grid2d_t, zval*torch.ones((grid2d_t.shape[0],1))],1)
            grid3d_slice = torch.tensor(grid3d).type(config.torch_type).to(device)
            estSlice = impl_volume(grid3d_slice).detach().cpu().numpy().reshape(config.n1,config.n2)
            pp = (estSlice)*1.
            plt.figure(1)
            plt.clf()
            plt.imshow(pp,cmap='gray')
            plt.savefig(os.path.join(config.path_save+"/training/volume_slice_{}.png".format(zz)))

    # TODO: save other info? Local def?
        # t_test_ = time.time()
        # with torch.no_grad():
        #     gr = grid_class.grid3d_t/2+0.5
        #     gr[:,2] *= n3/max(config.n1,config.n2) 
        #     gr[:,2] += 0.5-(n3/max(config.n1,config.n2))/2
        #     V_est = impl_volume(gr).type(config.torch_type).reshape(config.n1,config.n2,n3)/n3
        #     out = mrcfile.new(config.path_save+"/training/V_est_iter.mrc",np.moveaxis(V_est.detach().cpu().numpy(),2,0),overwrite=True)
        #     out.close() 
        #     out = mrcfile.new(config.path_save+"/training/V_true_iter.mrc",np.moveaxis(V_t.detach().cpu().numpy(),2,0),overwrite=True)
        #     out.close() 

        #     V_ours = V_est.detach().cpu().numpy()
        #     V_ours /= V_ours.sum()

        #     fsc_ours = utils_ricardo.FSC(V_,V_ours)
            
        #     if len(np.where(fsc_ours[:,0]>0.5)[0]) == 0:
        #         fsc_5 = 0.
        #     else:    
        #         fsc_5 = 1/x_fsc[np.where(fsc_ours[:,0]>0.5)][-1]
        #     if len(np.where(fsc_ours[:,0]>0.143)[0]) == 0:
        #         fsc_143 = 0.
        #     else:
        #         fsc_143 = 1/x_fsc[np.where(fsc_ours[:,0]>0.143)][-1]

        #     SNR_est_vol = reconstruction.SNR(V_.reshape(-1) , V_ours.reshape(-1))

        #     plt.figure(1)
        #     plt.clf()
        #     plt.plot(x_fsc,fsc_ours,label='Est.')
        #     plt.plot(x_fsc,fsc_FBP,label='FBP')
        #     plt.plot(x_fsc,fsc_FBP_no_deformed,label='FBP no deformed')
        #     plt.xlabel("1/Resolution")
        #     plt.ylabel("FSC")
        #     plt.legend()
        #     plt.savefig(os.path.join(config.path_save,'training','FSC_ep_{}.png'.format(ep)))

        #     plt.figure(1)
        #     plt.clf()
        #     plt.plot(np.array(loss_tot))
        #     plt.legend()
        #     plt.savefig(os.path.join(config.path_save,'training','loss_ep_{}.png'.format(ep)))



            # loss_current_epoch = np.mean(loss_tot[-Ntest:])
            # print('Epoch: {}, loss: {:2.3}, loss_avg: {:2.3}, SNR vol: {:2.3}, res. (Cref): {:2.3}, res. (143): {:2.3}'.format(
            #     ep,loss.item(),loss_current_epoch,SNR_est_vol, fsc_5, fsc_143))
            # l_fid = np.mean(loss_data_fidelity[-Ntest:])
            # l_v = np.mean(loss_regul_volume[-Ntest:])
            # l_sh = np.mean(loss_regul_shifts[-Ntest:])
            # l_rot = np.mean(loss_regul_rot[-Ntest:])
            # l_loc = np.mean(loss_regul_local_ampl[-Ntest:])
            # l_loc_smooth = np.mean(loss_regul_local_smooth[-Ntest:])
            # print("                  Loss data fidelity: {:2.3}, regul volume: {:2.3}, regul shifts: {:2.3}, regul inplane: {:2.3}, regul local: {:2.3}, time: {:2.3}".format(l_fid,
            #         l_v,l_sh,l_rot,l_loc,time.time()-t0))
            

            # err_shift, err_shift_init, err_rot, err_rot_init, err_local, err_local_init = reconstruction.computeDeformationScore(grid_class_us.grid2d_t,angles_t,
            #                                                                                                                         shift_est,rot_est,
            #                                                                                                                         implicit_deformation_list,
            #                                                                                                                         affine_tr,local_tr,
            #                                                                                                                         config.n1,scale=deformationScale)

            
        # with torch.no_grad():
        #     config.Nangles_tmp = 10
        #     for kk in range(config.Nangles_tmp):
        #         tmp_ind = np.round(np.linspace(0,config.Nangles-1,config.Nangles_tmp)).astype(np.uint8)
        #         angles_tmp = np.linspace(-angle_bound,angle_bound,config.Nangles_tmp)
        #         angles_tmp_t = torch.tensor(angles_tmp).type(config.torch_type).to(device)
        #         # proj_est = sample_implicit(impl_volume,grid_class_us.grid3d_t,angles_tmp_t[kk],rot_deform=None,
        #         #                            shift_deform=None,local_deform=None,scale=deformationScale).reshape(config.n1//us,config.n2//us,n3//us)*mollifier.mollify3d()
                

        #         rays, _, _ = generate_ray(grid_class.grid2d_t,angles_tmp_t[kk:kk+1],ray_length,randomZ = 2,zmax=z_max)
        #         rays = rays.unsqueeze(0)
        #         outputValues,support = sample_implicit_batch_lowComp(impl_volume,rays,angles_tmp_t[kk:kk+1],
        #             rot_deformSet=None,shift_deformSet=None,local_deformSet=None,
        #             scale=deformationScale,range=inputRange,zlimit=n3/max(config.n1,config.n2))

        #         support = support.reshape(outputValues.shape[0],outputValues.shape[1],-1)
        #         outputValues = outputValues.type(config.torch_type)
        #         proj_est = torch.sum(support*outputValues,2)/n3
        #         proj_est = proj_est.reshape(config.n1,config.n2)
        #         plt.figure(1)
        #         plt.clf()
        #         plt.subplot(3,1,1)
        #         plt.imshow((projections_clean[tmp_ind[kk]]).detach().cpu().numpy(),cmap='gray')
        #         plt.title("{}".format(angles_tmp[kk]))
        #         plt.subplot(3,1,2)
        #         plt.imshow((projections_noisy[tmp_ind[kk]]).detach().cpu().numpy(),cmap='gray')
        #         plt.subplot(3,1,3)
        #         plt.imshow((proj_est).detach().cpu().numpy(),cmap='gray')
        #         plt.savefig(os.path.join(config.path_save,'projections','training','recons_ep_{}_{}.png'.format(ep,kk)))

        #         tmp = (proj_est*mollifier.mollify2d()).detach().cpu().numpy()
        #         tmp[np.isfinite(tmp)==False] = 0
        #         tmp = (tmp - tmp.max())/(tmp.max()-tmp.min())
        #         tmp = np.floor(255*tmp).astype(np.uint8)
        #         imageio.imwrite(os.path.join(config.path_save,'projections','training','est_ep_{}_{}.png'.format(ep,kk)),tmp)

        #         ind_ = np.arange(0,n3,config.Nangles_tmp)
        #         tmp = (V_est[:,:,ind_[kk]]).detach().cpu().numpy()
        #         tmp[np.isfinite(tmp)==False] = 0
        #         tmp = (tmp - tmp.max())/(tmp.max()-tmp.min())
        #         tmp = np.floor(255*tmp).astype(np.uint8)
        #         imageio.imwrite(os.path.join(config.path_save,'projections','volume','est_ep_{}_{}.png'.format(ep,ind_[kk])),tmp)

            # if(use_deformation_estimation):
            #     print("   With est. deformations || Err shift: {:2.3}+/-{:2.3} -- Err rot: {:2.3}+/-{:2.3} -- Err local: {:2.3}+/-{:2.3}".format(err_shift.mean(),err_shift.std(),err_rot.mean(),err_rot.std(),err_local.mean(),err_local.std()))
            #     print("Without est. deformations || Err shift: {:2.3}+/-{:2.3} -- Err rot: {:2.3}+/-{:2.3} -- Err local: {:2.3}+/-{:2.3}".format(err_shift_init.mean(),err_shift_init.std(),err_rot_init.mean(),err_rot_init.std(),err_local_init.mean(),err_local_init.std()))

            #     kk = config.Nangles_//2
            #     utils_display.display_local(implicit_deformation_list[kk],field_true=local_tr[kk],Npts=(20,20),img_path=config.path_save+"deformations/training/quiver_middle_"+str(ep),
            #                                 img_type='.pdf',scale=1,alpha=0.8,width=0.002)
            #     if not os.path.exists(config.path_save+"deformations/training/"+str(ep)+"/"):
            #         os.makedirs(config.path_save+"deformations/training/"+str(ep)+"/")
            #     utils_display.display_local_movie(implicit_deformation_list,field_true=local_tr,Npts=(20,20),
            #                                         img_path=config.path_save+"deformations/training/"+str(ep)+"/",img_type='.png',
            #                                         scale=1/10,alpha=0.8,width=0.002)
                            
        # with torch.no_grad():
        #     for kk in range(len(choosenLocations_all)):

        #         plt.figure(1)
        #         plt.clf()
        #         plt.imshow(current_sampling[kk])
        #         plt.colorbar()

        #         # plt.clf()
        #         # for ii in range(len(choosenLocations_all[angles[kk]])):
        #         #     pts = choosenLocations_all[angles[kk]][ii]
        #         #     plt.scatter(pts[:,0],pts[:,1],s=1,c='b',marker='+')

        #         if not os.path.exists(config.path_save+"projections/sampling/"+str(ep)+"/"):
        #             os.makedirs(config.path_save+"projections/sampling/"+str(ep)+"/")
        #         plt.savefig(os.path.join(config.path_save,'projections','sampling',str(ep),'angle_{}.png'.format(kk)))
    if ep%config.NsaveNet ==0 and ep!=0:                    
        torch.save({
        'shift_est': shift_est,
        'rot_est': rot_est,
        'local_deformation_network': implicit_deformation_list,
        'implicit_volume': impl_volume.state_dict(),
        }, os.path.join(config.path_save, 'model_everything_joint_batch.pt'))


torch.save({
'shift_est': shift_est,
'rot_est': rot_est,
'local_deformation_network': implicit_deformation_list,
'implicit_volume': impl_volume.state_dict(),
}, os.path.join(config.path_save, 'model_everything_joint_batch.pt'))



