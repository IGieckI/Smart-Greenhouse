from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import joblib
import os

app = FastAPI()

MODEL_PATH = "/app/models/leaf_temp_model.pkl"

class SensorData(BaseModel):
    air_temp: float # BUG FIX

@app.post("/predict")
def predict_leaf_temp(data: SensorData):
    if not os.path.exists(MODEL_PATH):
        raise HTTPException(status_code=503, detail="Modello non ancora addestrato")
    
    try:
        model = joblib.load(MODEL_PATH)
        prediction = model.predict([[data.air_temp]]) # BUG FIX
        return {"predicted_leaf_temperature": round(prediction[0], 2)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))