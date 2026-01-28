from .loader import load_model
from .rag import retrieve_context

def generate_answer(question: str) -> str:
    tokenizer, model = load_model()

    context = retrieve_context(question)

    prompt = f"""
당신은 항공편 지연 및 결항 보상 전문 상담사입니다.

[관련 규정]
{context}

[질문]
{question}

[답변]
"""

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    outputs = model.generate(
        **inputs,
        max_new_tokens=256,
        temperature=0.3,
        do_sample=True,
    )

    return tokenizer.decode(outputs[0], skip_special_tokens=True)
