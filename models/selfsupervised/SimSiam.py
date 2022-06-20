import tensorflow as tf
import numpy as np

# Evaluation and Visualization lib.
from models.evaluation.latent_space import *
from models.evaluation.features import *

# Data/Folder Manipulation lib.
from data_manipulation.utils import *
from models.evaluation import *
from models.tools import *
from models.utils import *

# Network related lib.
from models.networks.encoder_contrastive import *
from models.data_augmentation import *
from models.normalization import *
from models.regularizers import *
from models.activations import *
from models.ops import *

# Losses and Optimizers.
from models.optimizer import *
from models.loss import *


class RepresentationsPathology():
	def __init__(self,
				data,                       			# Dataset type, training, validatio, and test data.
				z_dim,	                    			# Latent space dimensions.
				beta_1,                      			# Beta 1 value for Adam Optimizer.
				learning_rate_e,             			# Learning rate Encoder.
				temperature=0.1,                        # Temperature for contrastive consine similarity norm.
				spectral=True,							# Spectral Normalization for weights.
				layers=5,					 			# Number for layers for Encoder.
				attention=28,                			# Attention Layer dimensions, default after hegiht and width equal 28 to pixels.
				power_iterations=1,          			# Iterations of the power iterative method: Calculation of Eigenvalues, Singular Values.
				init = 'orthogonal',    			    # Weight Initialization: default Orthogonal.
				regularizer_scale=1e-4,      			# Orthogonal regularization.
				model_name='RepresentationsPathology'  	# Model Name.
				):

		### Input data variables.
		self.image_height   = data.patch_h
		self.image_width    = data.patch_w
		self.image_channels = data.n_channels
		self.batch_size     = data.batch_size

		### Architecture parameters.
		self.attention = attention
		self.layers    = layers
		self.spectral  = spectral
		self.z_dim     = z_dim
		self.init      = init

		### Hyperparameters.
		self.power_iterations  = power_iterations
		self.regularizer_scale = regularizer_scale
		self.learning_rate_e   = learning_rate_e
		self.beta_1            = beta_1
		self.temperature       = temperature

		### Data augmentation conditions.
		# Spatial transformation.
		self.crop          = True
		self.rotation      = True
		self.flip          = True
		# Color transofrmation.
		self.color_distort = True
		# Gaussian Blur and Noise.
		self.g_blur        = False
		self.g_noise       = False
		# Sobel Filter.
		self.sobel_filter  = False

		self.num_samples = data.training.images.shape[0]
		all_indx = list(range(self.num_samples))
		random.shuffle(all_indx)
		self.selected_indx = np.array(sorted(all_indx[:10000]))

		self.model_name = model_name

		self.build_model()

	# Self-supervised inputs.
	def model_inputs(self):

		# Image input for transformation.
		real_images_1 = tf.placeholder(dtype=tf.float32, shape=(None, self.image_width, self.image_height, self.image_channels), name='real_images_1')
		real_images_2 = tf.placeholder(dtype=tf.float32, shape=(None, self.image_width, self.image_height, self.image_channels), name='real_images_2')

		# Transformed images.
		transf_real_images_1 = tf.placeholder(dtype=tf.float32, shape=(None, self.image_width, self.image_height, self.image_channels), name='transf_real_images_1')
		transf_real_images_2 = tf.placeholder(dtype=tf.float32, shape=(None, self.image_width, self.image_height, self.image_channels), name='transf_real_images_2')
		# Learning rates.
		learning_rate_e = tf.placeholder(dtype=tf.float32, name='learning_rate_e')

		return real_images_1, real_images_2, transf_real_images_1, transf_real_images_2, learning_rate_e

	# Data Augmentation Layer.
	def data_augmentation_layer(self, images, crop, rotation, flip, g_blur, g_noise, color_distort, sobel_filter):
		images_trans = images

		# Spatial transformations.
		if crop:
		    images_trans = tf.map_fn(random_crop_and_resize_p075, images_trans)
		if rotation:
			images_trans = tf.map_fn(random_rotate, images_trans)
		if flip:
		    images_trans = random_flip(images_trans)

		# Gaussian blur and noise transformations.
		if g_blur:
			images_trans = tf.map_fn(random_blur, images_trans)
		if g_noise:
			images_trans = tf.map_fn(random_gaussian_noise, images_trans)

		# Color distorsions.
		if color_distort:
			images_trans = tf.map_fn(random_color_jitter_1p0, images_trans)

		# Sobel filter.
		if sobel_filter:
			images_trans = tf.map_fn(random_sobel_filter, images_trans)

		# Make sure the image batch is in the right format.
		images_trans = tf.reshape(images_trans, [-1, self.image_height, self.image_width, self.image_channels])
		images_trans = tf.clip_by_value(images_trans, 0., 1.)

		return images_trans


	# Encoder Network.
	def encoder(self, images, is_train, reuse, init, name, label_input=None):
		conv_space, h, z = encoder_resnet_contrastive_SimSiam(images=images, z_dim=self.z_dim, h_dim=1024, layers=self.layers, spectral=self.spectral, activation=ReLU, reuse=reuse, init=init,
															  is_train=is_train, normalization=batch_norm, regularizer=None, attention=self.attention, name=name)
		return conv_space, h, z


	def predictor(self, h_rep, is_train, reuse, init, name):
		q = byol_predictor(z_rep=h_rep, z_dim=h_rep.shape[1], spectral=self.spectral, h_dim=256, activation=ReLU, is_train=is_train, reuse=reuse, init=init, regularizer=None, normalization=batch_norm, name=name)
		return q

	# Loss Function.
	def loss(self):
		print(self.h_rep_t2.shape)
		print(self.prediction_h1_h2.shape)
		second_aug_no_grad = tf.stop_gradient(self.h_rep_t2)
		loss_mse = byol_loss(prediction=self.prediction_h1_h2, z_rep=second_aug_no_grad)
		return loss_mse

	# Optimizer.
	def optimization(self):
		with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS)):
			# Learning rate decay.
			global_step = tf.Variable(0, trainable=False)
			self.lr_decayed_fn = tf.train.polynomial_decay(learning_rate=self.learning_rate_input_e, global_step=global_step, decay_steps=3*self.num_samples, end_learning_rate=0.1*self.learning_rate_input_e,
														   power=0.5)
			#  Optimizer
			opt = tf.train.AdamOptimizer(learning_rate=self.lr_decayed_fn, beta1=self.beta_1)
			# Quick dirty optimizer for Encoder.
			trainable_variables = tf.trainable_variables()
			encoder_total_variable = list()
			encoder_online_variables    = [variable for variable in trainable_variables if variable.name.startswith('contrastive_encoder')]
			encoder_predictor_variables = [variable for variable in trainable_variables if variable.name.startswith('encoder_predictor')]
			encoder_total_variable.extend(encoder_online_variables)
			encoder_total_variable.extend(encoder_predictor_variables)
			train_encoder = opt.minimize(self.loss_mse, var_list=encoder_total_variable)
		return train_encoder

	# Build the Self-supervised.
	def build_model(self):
		################### INPUTS & DATA AUGMENTATION #####################################################################################################################################
		# Inputs.
		self.real_images_1, self.real_images_2, self.transf_real_images_1, self.transf_real_images_2, self.learning_rate_input_e = self.model_inputs()
		# Data augmentation layer.
		self.real_images_1_t1 = self.data_augmentation_layer(images=self.real_images_1, crop=self.crop, rotation=self.rotation, flip=self.flip, g_blur=self.g_blur, g_noise=self.g_noise,
															 color_distort=self.color_distort, sobel_filter=self.sobel_filter)
		self.real_images_1_t2 = self.data_augmentation_layer(images=self.real_images_1, crop=self.crop, rotation=self.rotation, flip=self.flip, g_blur=self.g_blur, g_noise=self.g_noise,
															 color_distort=self.color_distort, sobel_filter=self.sobel_filter)

		################### TRAINING #####################################################################################################################################
		# Encoder Training.
		self.conv_space_t1, self.h_rep_t1, self.z_rep_t1 = self.encoder(images=self.transf_real_images_1, is_train=True, reuse=False, init=self.init, name='contrastive_encoder')
		self.prediction_h1_h2 = self.predictor(h_rep=self.h_rep_t1, is_train=True, reuse=False, init=self.init, name='encoder_predictor')

		self.conv_space_t2, self.h_rep_t2, self.z_rep_t2 = self.encoder(images=self.transf_real_images_2, is_train=True, reuse=True,  init=self.init, name='contrastive_encoder')

		################### INFERENCE #####################################################################################################################################
		# Encoder Representations Inference.
		self.conv_space_out, self.h_rep_out, self.z_rep_out = self.encoder(images=self.real_images_2, is_train=False, reuse=True, init=self.init, name='contrastive_encoder')

		################### LOSS & OPTIMIZER ##############################################################################################################################
		# Losses.
		self.loss_mse = self.loss()
		# Optimizers.
		self.train_encoder  = self.optimization()

	def project_subsample(self, session, data, epoch, data_out_path, report, batch_size=50):

		# Handle directories and copies.
		results_path = os.path.join(data_out_path, 'results')
		epoch_path = os.path.join(results_path, 'epoch_%s' % epoch)
		check_epoch_path = os.path.join(epoch_path, 'checkpoints')
		checkpoint_path = os.path.join(results_path, '../checkpoints')
		os.makedirs(epoch_path)
		shutil.copytree(checkpoint_path, check_epoch_path)

		num_samples = 10000

		# Setup HDF5 file.
		hdf5_path = os.path.join(epoch_path, 'hdf5_epoch_%s_projected_images.h5' % epoch)
		hdf5_file = h5py.File(hdf5_path, mode='w')
		img_storage  = hdf5_file.create_dataset(name='images',           shape=[num_samples, data.patch_h, data.patch_w, data.n_channels], dtype=np.float32)
		conv_storage = hdf5_file.create_dataset(name='conv_features',    shape=[num_samples] + self.conv_space_t1.shape.as_list()[1:],     dtype=np.float32)
		h_storage    = hdf5_file.create_dataset(name='h_representation', shape=[num_samples] + self.h_rep_t1.shape.as_list()[1:],          dtype=np.float32)

		ind = 0
		while ind<num_samples:
			images_batch = data.training.images[self.selected_indx[ind: ind+batch_size], :, :, :]
			feed_dict = {self.real_images_2:images_batch}
			conv_space_out, h_rep_out = session.run([self.conv_space_out, self.h_rep_out], feed_dict=feed_dict)

			img_storage[ind: ind+batch_size, :, : ,:]  = images_batch
			conv_storage[ind: ind+batch_size, :] = conv_space_out
			h_storage[ind: ind+batch_size, :]    = h_rep_out
			ind += batch_size
		try:
			report_progress_latent(epoch=epoch, w_samples=conv_storage, img_samples=img_storage, img_path=hdf5_path.split('/hdf5')[0], storage_name='conv_lat')
			report_progress_latent(epoch=epoch, w_samples=h_storage,    img_samples=img_storage, img_path=hdf5_path.split('/hdf5')[0], storage_name='h_lat')
		except:
			print('Issue printing latent space images. Epoch', epoch)
		finally:
			os.remove(hdf5_path)


	# Training function.
	def train(self, epochs, data_out_path, data, restore, print_epochs=10, n_images=25, checkpoint_every=None, report=False):
		run_epochs = 0
		saver = tf.train.Saver()

		# Setups.
		checkpoints, csvs = setup_output(data_out_path=data_out_path, model_name=self.model_name, restore=restore)
		losses = ['Contrastive Loss']
		setup_csvs(csvs=csvs, model=self, losses=losses)
		report_parameters(self, epochs, restore, data_out_path)

		# Session Options.
		config = tf.ConfigProto()
		config.gpu_options.allow_growth = True
		run_options = tf.RunOptions(report_tensor_allocations_upon_oom=True)

		# Training session.
		with tf.Session(config=config) as session:
			session.run(tf.global_variables_initializer())

			# Restore previous session.
			if restore:
				check = get_checkpoint(data_out_path)
				saver.restore(session, check)
				print('Restored model: %s' % check)

			# Example of augmentation images.
			batch_images, batch_labels = data.training.next_batch(n_images)
			data.training.reset()
			feed_dict = {self.real_images_1:batch_images, self.real_images_2:batch_images}
			transf_real_images_1, transf_real_images_2 = session.run([self.real_images_1_t1, self.real_images_1_t2], feed_dict=feed_dict, options=run_options)
			write_sprite_image(filename=os.path.join(data_out_path, 'images/transformed_1.png'), data=transf_real_images_1, metadata=False)
			write_sprite_image(filename=os.path.join(data_out_path, 'images/transformed_2.png'), data=transf_real_images_2, metadata=False)

			# Epoch Iteration.
			for epoch in range(1, epochs+1):

				# Batch Iteration.
				for batch_images, batch_labels in data.training:

					################################# DISCRIMINATOR & GENERATOR ###############################################
					# Update discriminator.
					feed_dict = {self.real_images_1:batch_images, self.real_images_2:batch_images}
					transf_real_images_1, transf_real_images_2 = session.run([self.real_images_1_t1, self.real_images_1_t2], feed_dict=feed_dict, options=run_options)
					feed_dict = {self.real_images_1:batch_images, self.real_images_2:batch_images, self.transf_real_images_1:transf_real_images_1,
								 self.transf_real_images_2:transf_real_images_2, self.learning_rate_input_e: self.learning_rate_e}
					session.run([self.train_encoder], feed_dict=feed_dict, options=run_options)

					####################################################################################################
					# Print losses and Generate samples.
					if run_epochs % print_epochs == 0:
						model_outputs = [self.loss_mse]
						epoch_outputs = session.run(model_outputs, feed_dict=feed_dict, options=run_options)
						update_csv(model=self, file=csvs[0], variables=epoch_outputs, epoch=epoch, iteration=run_epochs, losses=losses)
					run_epochs += 1
					# break

				# Save model.
				saver.save(sess=session, save_path=checkpoints)
				data.training.reset()

				############################### FID TRACKING ##################################################
				# Save checkpoint and generate images for FID every X epochs.
				if (checkpoint_every is not None and epoch % checkpoint_every == 0) or (epochs==epoch):
					self.project_subsample(session=session, data=data, epoch=epoch, data_out_path=data_out_path, report=report)
