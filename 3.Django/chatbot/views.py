import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from chatbot.llm.chain import answer_question
from .llm.chain import answer_question

@csrf_exempt  # 데모 단계에서는 편하게. (나중에 CSRF 적용 가능)
@require_POST
def api_chat(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "invalid_json"}, status=400)

    message = (payload.get("message") or "").strip()
    airport = (payload.get("airport") or "").strip().upper() or None
    if not message:
        return JsonResponse({"error": "empty_message"}, status=400)

    try:
        reply = answer_question(message, airport=airport)
        return JsonResponse({"reply": reply})
    except Exception as e:
        # 데모용: 에러 숨기고 메시지만
        return JsonResponse({"error": "chat_failed", "detail": str(e)}, status=500)
