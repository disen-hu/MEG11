import torch
import torch.nn as nn
import torch.nn.functional as F
import pytorch_lightning as pl
from sklearn.metrics import precision_recall_fscore_support
from torch.autograd import Variable


class MEGConvNet(pl.LightningModule):
    """
    MEGConvNet - Convolutional Neural Network, each block contains 1-d convolution, batch normalization, leaky ReLU and max pooling.
    """
    def __init__(self, config):
        super(MEGConvNet, self).__init__()
        self.config = config

        # calculates input size for the final fully connected layer
        if config['window_size'] != -1:
            input_size = config['window_size']
        else:
            input_size = config['n_timesteps']//config['downsample']
        
        current_size = input_size
        for _ in range(config['n_layers']):
            # calculates the size after convolution
            current_size = ((current_size - config['kernel_size'] + 2 * config['padding']) // config['stride']) + 1
            # calculates the size after max pooling
            current_size = (current_size - config['pooling_size']) // config['pooling_size'] + 1
        
        # calculates the input size for the fully connected layer
        fc_input_size = current_size * config['out_channels']

        # defines convolutional layers
        layers = []
        in_channels = config['n_sensors']
        for _ in range(config['n_layers']):
            layers += [
                nn.Conv1d(in_channels, config['out_channels'], config['kernel_size'], stride=config['stride'], padding=config['padding']),
                nn.BatchNorm1d(config['out_channels']),
                nn.LeakyReLU(negative_slope=config['negative_slope']),
                nn.MaxPool1d(config['pooling_size'])
            ]
            in_channels = config['out_channels']

        self.conv_layers = nn.Sequential(*layers)

        # defines final fully connected layer
        self.fc = nn.Linear(fc_input_size, config['num_classes'])
        
        # defines loss criterion and initialises variables for training curves
        self.train_loss = 0.0
        self.val_loss = 0.0
        self.val_progress = []
        self.best_acc = 0.0
        self.best_f1 = 0.0

    def forward(self, x):
        """
        Performs a forward pass of the defined neural network.
        """
        x = self.conv_layers(x)
        x = torch.flatten(x, 1)
        x = self.fc(x)
        return x

    # def training_step(self, batch, batch_idx):
    #     """
    #     Training step for MEGConvNet.

    #     :param batch: Batch of training data.
    #     :param batch_idx: Index of the current batch.
    #     :returns: loss: The training loss.
    #     """
    #     x, y = batch
    #     logits = self(x)
    #     loss = F.cross_entropy(logits, y)
    #     self.train_loss += loss.item()
    #     return loss



    def training_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.cross_entropy(logits, y)
        self.train_loss += loss.item()

        # 收集预测和标签
        pred = torch.argmax(F.softmax(logits, dim=1), dim=1)
        if hasattr(self, 'train_preds'):
            self.train_preds = torch.cat((self.train_preds, pred), dim=0)
            self.train_labels = torch.cat((self.train_labels, y), dim=0)
        else:
            self.train_preds = pred
            self.train_labels = y

        return loss

    def on_train_epoch_end(self):
        # 在训练周期结束时计算准确率
        acc = torch.sum(self.train_preds == self.train_labels).item() / len(self.train_labels)
        if self.config['log'] in ['wandb', 'all']:
#             wandb.log({'train_acc': acc})
        
         if self.config['log'] in ['stdout', 'all']:
               print(f'Training accuracy: {acc}')

        # 重置变量以进行下一个周期
        delattr(self, 'train_preds')
        delattr(self, 'train_labels')

    def validation_step(self, batch, batch_idx):
        """
        Validation step for MEGConvNet.

        :param batch: Batch of training data.
        :param batch_idx: Index of the current batch.
        :returns: loss: The validation loss.
        """
        x, y = batch
        logits = self(x)
        loss = F.cross_entropy(logits, y)
        self.log('val_loss', loss)
        pred = F.softmax(logits, dim=1)
        self.val_loss += loss.item()
        self.val_progress.append((pred, y))
        return loss
    
    def on_validation_epoch_end(self):
        """
        Validation callback for pl trainer.
        Calculates and logs train loss and validation metrics.
        """
        # calculate accuracy on full validation set
        preds = torch.cat([pred for pred, y in self.val_progress], dim=0)
        y = torch.cat([y for pred, y in self.val_progress], dim=0)
        preds = torch.argmax(preds, dim=1)
        acc = torch.sum(preds == y).item() / len(y)
        precision, recall, f1, _ = precision_recall_fscore_support(y.cpu(), preds.cpu(), average='macro')

        # log metrics based on configuration settings
        if self.config['log'] in ['wandb', 'all']:
#             wandb.log({'train_loss': self.train_loss, 'val_loss': self.val_loss, 'val_acc': acc, 'val_precision': precision, 'val_recall': recall, 'val_f1': f1})
        
         if self.config['log'] in ['stdout', 'all']:
            print(f'Training loss: {self.train_loss}')
            print(f'Validation accuracy: {acc}')
            print(f'Validation loss: {self.val_loss}')
            print(f'Validation precision: {precision}')
            print(f'Validation recall: {recall}')
            print(f'Validation f1: {f1}')
        
        # saved best model, based on chosen target metric if save_best is set to True
        if self.config['save_best']:
            if self.config['target_metric'] == 'accuracy' and acc > self.best_acc:
                self.best_acc = acc
                save_checkpoint(self, 'weights/'+self.config['run_name']+'.pth')
            elif self.config['target_metric'] == 'f1' and f1 > self.best_f1:
                self.best_f1 = f1
                save_checkpoint(self, 'weights/'+self.config['run_name']+'.pth')
        
         # reset variables for next epoch
        self.val_progress = []
        self.val_loss = 0.0
        self.train_loss = 0.0

    def configure_optimizers(self):
        # initialize Adam optimizer and scheduler with warmup
        self.optimizer = torch.optim.Adam(self.parameters(), lr=self.config['lr'])
        self.scheduler = torch.optim.lr_scheduler.OneCycleLR(self.optimizer, max_lr=self.config['lr'], steps_per_epoch=self.config['steps_per_epoch'], epochs=self.config['epochs'])
        return [self.optimizer], [self.scheduler]


class LSTMNet(pl.LightningModule):
    def __init__(self, config):
        super(LSTMNet, self).__init__()
        self.config = config

        # calculates input size for the final fully connected layer
        if config['window_size'] != -1:
            input_size = config['window_size'] 
        else:
            input_size = config['n_timesteps']//config['downsample']
        
        self.hidden_size = config['lstm_hidden_size']
        self.lstm = nn.LSTM(input_size= config['n_sensors'],
                            hidden_size= self.hidden_size,
                            num_layers = 1,
                            batch_first =True
                            )
        virtual_input_sequence = torch.randn(config['batch_size'], input_size, config['n_sensors'])
        lstm_output, _ = self.lstm(virtual_input_sequence)
        lstm_output_size = lstm_output.size()

        current_size = lstm_output_size[1]
        for _ in range(config['n_layers']):
            # calculates the size after convolution
            current_size = ((current_size - config['kernel_size'] + 2 * config['padding']) // config['stride']) + 1
            # calculates the size after max pooling
            current_size = (current_size - config['pooling_size']) // config['pooling_size'] + 1
        # calculates the input size for the fully connected layer
        fc_input_size = current_size * config['out_channels']

        # defines convolutional layers
        layers = []
        in_channels = self.hidden_size
        for _ in range(config['n_layers']):
            layers += [
                nn.Conv1d(in_channels, config['out_channels'], config['kernel_size'], stride=config['stride'], padding=config['padding']),
                nn.BatchNorm1d(config['out_channels']),
                nn.LeakyReLU(negative_slope=config['negative_slope']),
                nn.MaxPool1d(config['pooling_size'])
            ]
            in_channels = config['out_channels']
        self.conv_layers = nn.Sequential(*layers)

        self.fc = nn.Linear(fc_input_size, config['num_classes'])
        
        # defines loss criterion and initialises variables for training curves
        self.train_loss = 0.0
        self.val_loss = 0.0
        self.val_progress = []
        self.best_acc = 0.0
        self.best_f1 = 0.0

    def forward(self, x):
        """
        Performs a forward pass of the defined neural network.
        """
        # h_0 = Variable(torch.randn(1, x.size(0), self.hidden_size, device="cuda:0")) 
        # c_0 = Variable(torch.randn(1, x.size(0), self.hidden_size, device="cuda:0"))
        x,_ = self.lstm(x)
        x = x.transpose(1,2)
        x = self.conv_layers(x)
        x = x.transpose(1,2)
        x = torch.flatten(x, 1)
        x = self.fc(x)
        return x

    # def training_step(self, batch, batch_idx):
    #     """
    #     Training step for MEGConvNet.

    #     :param batch: Batch of training data.
    #     :param batch_idx: Index of the current batch.
    #     :returns: loss: The training loss.
    #     """
    #     x, y = batch
    #     logits = self(x)
    #     loss = F.cross_entropy(logits, y)
    #     self.train_loss += loss.item()
    #     return loss

    def training_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.cross_entropy(logits, y)
        self.train_loss += loss.item()

        # 收集预测和标签
        pred = torch.argmax(F.softmax(logits, dim=1), dim=1)
        if hasattr(self, 'train_preds'):
            self.train_preds = torch.cat((self.train_preds, pred), dim=0)
            self.train_labels = torch.cat((self.train_labels, y), dim=0)
        else:
            self.train_preds = pred
            self.train_labels = y

        return loss

    def on_train_epoch_end(self):
        # 在训练周期结束时计算准确率
        acc = torch.sum(self.train_preds == self.train_labels).item() / len(self.train_labels)
#         if self.config['log'] in ['wandb', 'all']:
# #             wandb.log({'train_acc': acc})
        
#          if self.config['log'] in ['stdout', 'all']:
#             print(f'Training accuracy: {acc}')

        # 重置变量以进行下一个周期
        delattr(self, 'train_preds')
        delattr(self, 'train_labels')

    def validation_step(self, batch, batch_idx):
        """
        Validation step for MEGConvNet.

        :param batch: Batch of training data.
        :param batch_idx: Index of the current batch.
        :returns: loss: The validation loss.
        """
        x, y = batch
        logits = self(x)
        loss = F.cross_entropy(logits, y)
        self.log('val_loss', loss)
        pred = F.softmax(logits, dim=1)
        self.val_loss += loss.item()
        self.val_progress.append((pred, y))
        return loss
    
    def on_validation_epoch_end(self):
        """
        Validation callback for pl trainer.
        Calculates and logs train loss and validation metrics.
        """
        # calculate accuracy on full validation set
        preds = torch.cat([pred for pred, y in self.val_progress], dim=0)
        y = torch.cat([y for pred, y in self.val_progress], dim=0)
        preds = torch.argmax(preds, dim=1)
        acc = torch.sum(preds == y).item() / len(y)
        precision, recall, f1, _ = precision_recall_fscore_support(y.cpu(), preds.cpu(), average='macro')

        # log metrics based on configuration settings
#         if self.config['log'] in ['wandb', 'all']:
# #             wandb.log({'train_loss': self.train_loss, 'val_loss': self.val_loss, 'val_acc': acc, 'val_precision': precision, 'val_recall': recall, 'val_f1': f1})
        
#          if self.config['log'] in ['stdout', 'all']:
#             print(f'Training loss: {self.train_loss}')
#             print(f'Validation accuracy: {acc}')
#             print(f'Validation loss: {self.val_loss}')
#             print(f'Validation precision: {precision}')
#             print(f'Validation recall: {recall}')
#             print(f'Validation f1: {f1}')
        
        # saved best model, based on chosen target metric if save_best is set to True
        if self.config['save_best']:
            if self.config['target_metric'] == 'accuracy' and acc > self.best_acc:
                self.best_acc = acc
                save_checkpoint(self, 'weights/' + str(self.config['run_name']) + '.pth')

            elif self.config['target_metric'] == 'f1' and f1 > self.best_f1:
                self.best_f1 = f1
                save_checkpoint(self, 'weights/' + str(self.config['run_name']) + '.pth')

        
         # reset variables for next epoch
        self.val_progress = []
        self.val_loss = 0.0
        self.train_loss = 0.0

    def configure_optimizers(self):
        # initialize Adam optimizer and scheduler with warmup
        self.optimizer = torch.optim.Adam(self.parameters(), lr=self.config['lr'])
        self.scheduler = torch.optim.lr_scheduler.OneCycleLR(self.optimizer, max_lr=self.config['lr'], steps_per_epoch=self.config['steps_per_epoch'], epochs=self.config['epochs'])
        return [self.optimizer], [self.scheduler]





def save_checkpoint(model, path):
    """
    Saves a checkpoint, optimizer, and scheduler from a specified path.

    :param model: The model to load the checkpoint into.
    :param path: The file path from which to load the checkpoint.
    """
    torch.save({
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': model.optimizer.state_dict(),
        'scheduler_state_dict': model.scheduler.state_dict()
    }, path)


def load_checkpoint(model, path):
    """
    Loads a checkpoint, optimizer, and scheduler from a specified path.

    :param model: The model to load the checkpoint into.
    :param path: The file path from which to load the checkpoint.
    """
    checkpoint = torch.load(path)
    model.load_state_dict(checkpoint['model_state_dict'])
    model.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    model.scheduler.load_state_dict(checkpoint['scheduler_state_dict'])