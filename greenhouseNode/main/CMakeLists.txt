idf_component_register(
    SRCS "main copy.cpp" "main.cpp"
    INCLUDE_DIRS "."
    REQUIRES tds DS18B20 bh1750 aht20_bmp280 soilMoisture esp_wifi esp_netif esp_event nvs_flash
)