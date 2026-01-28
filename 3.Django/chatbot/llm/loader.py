from transformers import AutoTokenizer, AutoModelForCausalLM
import torch

MODEL_NAME = "your-hf-model-name"  # ← 여기에 모델명

_tokenizer = None
_model = None

def load_model():
    global _tokenizer, _model

    if _model is None:
        print("[LLM] loading model...")
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            device_map="auto",
            torch_dtype=torch.float16,
        )
        print("[LLM] model loaded")

    return _tokenizer, _model
