TASKS = {
    "t1": {
        "target": "leaf_temp",
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux'],
        "use_lags": False,
        "lag_target": False, 
        "horizon_minutes": 0, # Stima puntuale (sostituisce steps: 1)
        "description": "Stima puntuale (adesso) senza dipendenze storiche."
    },
    "t2": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": False, 
        "horizon_minutes": 180, # Orizzonte 3 ore assolute (sostituisce steps: 30)
        "description": "Forecasting a 3h dipendente dall'ambiente passato e previsto, senza storico target."
    },
    "t3": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": True,  
        "horizon_minutes": 180,
        "description": "Forecasting a 3h pienamente autoregressivo."
    },
    "t4": {
        "target": "leaf_temp",
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": False,
        "lag_target": False, 
        "horizon_minutes": 0,
        "description": "As t1, but without tds."
    },
    "t5": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": False, 
        "horizon_minutes": 180,
        "description": "As t2, but without tds."
    },
    "t6": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": True,  
        "horizon_minutes": 180,
        "description": "As t3, but without tds."
    }
}