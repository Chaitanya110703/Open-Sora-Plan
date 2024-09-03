from torch import nn

from .dits import VideoDiT, Latte, STDiT

PREDICTOR_MODEL_MAPPINGS = {
    "dit": VideoDiT,
    "latte": Latte,
    "stdit": STDiT,
}


class PredictModel(nn.Module):
    """
    The backnone of the denoising model
    PredictModel is the factory class for all unets and dits

    Args:
        config[dict]: for Instantiating an atomic methods
    """

    def __init__(self, config):
        super().__init__()
        model_cls = PREDICTOR_MODEL_MAPPINGS[config.model_id]
        self.predictor = model_cls(**config.to_dict())

    def get_model(self):
        return self.predictor
