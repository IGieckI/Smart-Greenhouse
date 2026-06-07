idf_component_register(SRCS "main.c" "bh1750.c"
                       INCLUDE_DIRS "include"
                       REQUIRES "driver" "esp_timer")