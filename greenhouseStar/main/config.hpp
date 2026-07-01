// Heltec V3 specific pins
#define VEXT_PIN GPIO_NUM_36
#define OLED_SDA GPIO_NUM_17
#define OLED_SCL GPIO_NUM_18
#define OLED_RST GPIO_NUM_21

// SRAM Ring Buffer (16KB ~ 12sh )
#define INTERNAL_BUFFER_SIZE (16 * 1024) 

// If running the greenhouseStar on a Heltec board (with the display) 
// should be left enabled, comment it otherwire
#define IS_HELTEC

#ifdef IS_HELTEC
    #define VEXT_PIN   GPIO_NUM_36
    #define OLED_SDA   GPIO_NUM_17
    #define OLED_SCL   GPIO_NUM_18
    #define OLED_RST   GPIO_NUM_21

    #define LORA_CS    8
    #define LORA_SCK   9
    #define LORA_MOSI  10
    #define LORA_MISO  11
    #define LORA_RST   12
    #define LORA_BUSY  13
    #define LORA_DIO1  14
#else
    #define LORA_CS    5
    #define LORA_SCK   18
    #define LORA_MOSI  23
    #define LORA_MISO  19
    #define LORA_RST   14
    #define LORA_BUSY  27
    #define LORA_DIO1  26
#endif

