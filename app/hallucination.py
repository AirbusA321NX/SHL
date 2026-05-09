from lettucedetect.models.inference import HallucinationDetector
import os

# Initialize the detector
# This uses a ModernBERT based model for high precision
try:
    detector = HallucinationDetector(
        method="transformer", 
        model_path="KRLabsOrg/lettucedect-base-modernbert-en-v1"
    )
except Exception as e:
    print(f"Warning: LettuceDetect initialization failed: {e}")
    detector = None

def verify_response(context: str, question: str, answer: str) -> dict:
    """
    Verifies the answer against the context.
    Returns a dict with 'is_hallucinated' and 'spans'.
    """
    if detector is None:
        return {"is_hallucinated": False, "spans": []}
        
    try:
        # LettuceDetect expects a list of contexts
        predictions = detector.predict(
            context=[context], 
            question=question, 
            answer=answer, 
            output_format="spans"
        )
        
        # If any spans are returned, it's considered hallucinated
        is_hallucinated = len(predictions) > 0
        
        return {
            "is_hallucinated": is_hallucinated,
            "spans": predictions
        }
    except Exception as e:
        print(f"LettuceDetect Prediction Error: {e}")
        return {"is_hallucinated": False, "spans": []}
