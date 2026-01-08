from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class VoiceRequest(BaseModel):
    buyer_id: str
    quantity_needed: int
    delivery_time_days: int

@router.post("/voice-agent/start")
def start_voice_agent(req: VoiceRequest):
    # Step 1: Ask demand
    question_1 = f"Buyer {req.buyer_id}, how many units do you need?"
    
    # Step 2: Shelf-life decision
    if req.delivery_time_days > 3:
        decision = "Rejected: delivery time exceeds shelf-life"
    else:
        decision = "Accepted: export confirmed"

    return {
        "agent": "GreenChain Voice Agent",
        "question": question_1,
        "decision": decision,
        "payment_terms": "Net 7 days"
    }
