from __future__ import annotations
from typing import TYPE_CHECKING

import pytorch_lightning as pl
import torch
import torchmetrics.functional as Fmetrics

from .utils import check_loss, check_model, check_optimizer

if TYPE_CHECKING:
    from typing import Any, Callable, Mapping, Optional, Union
    from torch import Tensor


class GenericPredictionSystem(pl.LightningModule):
    r"""
    Generic prediction system providing standard methods.

    Parameters
    ----------
    model: torch.nn.Module
        Model to use.
    loss: torch.nn.modules.loss._Loss
        Loss used to fit the model.
    optimizer: torch.optim.Optimizer
        Optimization algorithm used to train the model.
    metrics: dict
        Dictionary containing the metrics to monitor during the training and
        to compute at test time.
    """

    def __init__(
        self,
        model: Union[torch.nn.Module, Mapping],
        loss: torch.nn.modules.loss._Loss,
        optimizer: torch.optim.Optimizer,
        metrics: Optional[dict[str, Callable]] = None,
        save_hyperparameters: Optional[bool] = True
    ):
        if save_hyperparameters:
            self.save_hyperparameters(ignore=['model', 'loss'])
        # Must be placed before the super call (or anywhere in other inheriting
        # class of GenericPredictionSystem). Otherwise the script pauses
        # indefinitely after returning self.optimizer. It is unclear why.

        super().__init__()
        self.model = check_model(model)
        self.optimizer = check_optimizer(optimizer)
        self.loss = check_loss(loss)
        self.metrics = metrics or {}

    def forward(self, x: Any) -> Any:
        return self.model(x)

    def _step(
        self, split: str, batch: tuple[Any, Any], batch_idx: int
    ) -> Union[Tensor, dict[str, Any]]:
        if split == "train":
            log_kwargs = {"on_step": False, "on_epoch": True}
        else:
            log_kwargs = {}

        x, y = batch
        y_hat = self(x)

        loss = self.loss(y_hat, y)
        self.log(f"{split}_loss", loss, **log_kwargs)

        for metric_name, metric_func in self.metrics.items():
            score = metric_func(y_hat, y)
            self.log(f"{split}_{metric_name}", score, **log_kwargs)

        return loss

    def training_step(
        self, batch: tuple[Any, Any], batch_idx: int
    ) -> Union[Tensor, dict[str, Any]]:
        return self._step("train", batch, batch_idx)

    def validation_step(
        self, batch: tuple[Any, Any], batch_idx: int
    ) -> Union[Tensor, dict[str, Any]]:
        return self._step("val", batch, batch_idx)

    def test_step(
        self, batch: tuple[Any, Any], batch_idx: int
    ) -> Union[Tensor, dict[str, Any]]:
        return self._step("test", batch, batch_idx)

    def predict_step(self, batch, batch_idx, dataloader_idx=0):
        x, y = batch
        return self(x)

    def configure_optimizers(self) -> torch.optim.Optimizer:
        return self.optimizer

    @staticmethod
    def state_dict_replace_key(
        state_dict: dict,
        replace: Optional[list[str]] = ['.', '']
    ):
        """Replace keys in a state_dict dictionnary.

        A state_dict usually is an OrderedDict where the keys are the model's
        module names. This method allows to change these names by replacing
        a given string, or token, by another.

        Parameters
        ----------
        state_dict : dict
            Model state_dict
        replace : Optional[list[str]], optional
            Tokens to replace in the
            state_dict module names. The first element is the token
            to look for while the second is the replacement value.
            By default ['.', ''].

        Returns
        -------
        dict
            State_dict with new keys.

        Examples
        -------
        I have loaded a Resnet18 model through a checkpoint after a training
        session. But the names of the model modules have been altered with a
        prefix "model.":

        >>> sd = model.state_dict()
        >>> print(list(sd)[:2])
        (model.conv1): Conv2d(3, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)
        (model.bn1): BatchNorm2d(64, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True)

        To remove this prefix:

        >>> sd = GenericPredictionSystem.state_dict_replace_key(sd, ['model.', ''])
        >>> print(sd[:2])
        (conv1): Conv2d(3, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)
        (bn1): BatchNorm2d(64, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True)
        """
        replace[0] += '.' if not replace[0].endswith('.') else ''
        for key in list(state_dict):
            print(key)
            state_dict[key.replace(replace[0], replace[1])] = state_dict.pop(key)
        return state_dict

    def predict(
        self,
        datamodule,
        trainer,
    ):
        """Predict a model's output on the test dataset.

        This method performs inference on the test dataset using only
        pytorchlightning tools.

        Parameters
        ----------
        datamodule : pl.LightningDataModule
            pytorchlightning datamodule handling all train/test/val datasets.
        trainer : pl.Trainer
            pytorchlightning trainer in charge of running the model on train
            and inference mode.

        Returns
        -------
        array
            Predicted tensor values.
        """
        datamodule.setup(stage="test")
        predictions = trainer.predict(datamodule=datamodule, model=self)
        predictions = torch.cat(predictions)
        return predictions

    def predict_point(
        self,
        checkpoint_path: str,
        data: Union[Tensor, tuple[Any, Any]],
        state_dict_replace_key: Optional[list[str, str]] = None,
        ckpt_transform: Callable = None
    ):
        """Predict a model's output on 1 data point.

        Performs as predict() but for a single data point and using native
        pytorch tools.

        Parameters
        ----------
        checkpoint_path : str
            path to the model's checkpoint to load.
        data : Union[Tensor, tuple[Any, Any]]
            data point to perform inference on.
        state_dict_replace_key : Optional[list[str, str]], optional
            list of values used to call the static method
            state_dict_replace_key(). Defaults to None.
        ckpt_transform : Callable, optional
            callable function applied to the loaded checkpoint object.
            Use this to modify the structure of the loaded model's checkpoint
            on the fly. Defaults to None.

        Returns
        -------
        array
            Predicted tensor value.
        """
        ckpt = torch.load(checkpoint_path)
        ckpt['state_dict'] = self.state_dict_replace_key(ckpt['state_dict'],
                                                         state_dict_replace_key)
        if ckpt_transform:
            ckpt = ckpt_transform(ckpt)
        self.model.load_state_dict(ckpt['state_dict'])
        self.model.eval()
        with torch.no_grad():
            prediction = self.model(data)
        return prediction


class FinetuningClassificationSystem(GenericPredictionSystem):
    r"""
    Basic finetuning classification system.

    Parameters
    ----------
        model: model to use
        lr: learning rate
        weight_decay: weight decay value
        momentum: value of momentum
        nesterov: if True, uses Nesterov's momentum
        metrics: dictionnary containing the metrics to compute
        binary: if True, uses binary classification loss instead of multi-class one
    """

    def __init__(
        self,
        model: Union[torch.nn.Module, Mapping],
        lr: float = 1e-2,
        weight_decay: float = 0,
        momentum: float = 0.9,
        nesterov: bool = True,
        metrics: Optional[dict[str, Callable]] = None,
        binary: bool = False,
    ):
        self.lr = lr
        self.weight_decay = weight_decay
        self.momentum = momentum
        self.nesterov = nesterov

        model = check_model(model)

        optimizer = torch.optim.SGD(
            model.parameters(),
            lr=self.lr,
            weight_decay=self.weight_decay,
            momentum=self.momentum,
            nesterov=self.nesterov,
        )
        if binary:
            loss = torch.nn.BCEWithLogitsLoss()
        else:
            loss = torch.nn.CrossEntropyLoss()

        if metrics is None:
            metrics = {
                "accuracy": Fmetrics.accuracy,
            }

        super().__init__(model, loss, optimizer, metrics)
