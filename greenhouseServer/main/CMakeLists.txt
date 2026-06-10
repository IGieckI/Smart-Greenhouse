idf_component_register(SRCS "main.cpp"
                       INCLUDE_DIRS "."
                       REQUIRES coap esp_wifi nvs_flash esp_event driver esp_timer jgromes__RadioLib)