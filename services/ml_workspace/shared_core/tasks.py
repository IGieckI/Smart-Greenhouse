TASKS = {
    "t1": {
        "target": "leaf_temp",
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux'],
        "use_lags": False,
        "lag_target": False, 
        "horizon_minutes": 0, 
        "description": "Point estimation (now) without historical dependencies."
    },
    "t2": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": False, 
        "horizon_minutes": 180, 
        "description": "3-hour forecasting dependent on past and predicted environment, without historical target."
    },
    "t3": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": True,  
        "horizon_minutes": 180,
        "description": "Fully autoregressive 3-hour forecasting."
    },
    "t4": {
        "target": "leaf_temp",
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": False,
        "lag_target": False, 
        "horizon_minutes": 0,
        "description": "Same as T1, but excluding TDS."
    },
    "t5": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": False, 
        "horizon_minutes": 180,
        "description": "Same as T2, but excluding TDS."
    },
    "t6": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": True,  
        "horizon_minutes": 180,
        "description": "Same as T3, but excluding TDS."
    }
}