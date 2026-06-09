idf_component_register(
    SRCS "main.cpp"
    INCLUDE_DIRS "."
    REQUIRES tds DS18B20 bh1750 aht20_bmp280 soilMoisture
)