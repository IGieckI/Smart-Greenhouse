
ENV_FEATURES = ['air_temp', 'humidity', 'pressure', 'water_temp', 'tds', 'soil_moisture', 'light_lux']

TASKS = {
    "t1": {
        "target": "leaf_temp",
        "features": ENV_FEATURES,
        "use_lags": False,
        "lag_target": False, 
        "description": "Point estimation (now) without historical dependencies."
    },
    "t2": {
        "target": "leaf_temp", 
        "features": ENV_FEATURES,
        "use_lags": True,  
        "lag_target": False, 
        "description": "Estimation at time T dependent on past environment, without historical target."
    },
    "t3": {
        "target": "leaf_temp", 
        "features": ENV_FEATURES,
        "use_lags": True,  
        "lag_target": True,  
        "description": "Fully autoregressive estimation at time T."
    },
    "t4": {
        "target": "leaf_temp",
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": False,
        "lag_target": False, 
        "description": "Same as T1, but excluding TDS."
    },
    "t5": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": False, 
        "description": "Same as T2, but excluding TDS."
    },
    "t6": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": True,  
        "description": "Same as T3, but excluding TDS."
    },
    "t8": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": False, 
        "lags": 15,
        "description": "Like T5, but using only 15 points of history."
    },
    "t9": {
        "target": "leaf_temp", 
        "features": ['air_temp', 'humidity', 'pressure', 'water_temp', 'soil_moisture', 'light_lux'],
        "use_lags": True,  
        "lag_target": True,  
        "lags": 15,
        "description": "Like T6, but fully autoregressive using 15 points of history."
    }
}


GROUPS = {
    "A" : ("t1", "t2", "t3"),
    "B" : ("t4", "t5", "t6"),
    "C" : ("t4", "t8", "t9")
}
