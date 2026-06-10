#ifndef ESP_HAL_H
#define ESP_HAL_H

#include <RadioLib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "rom/ets_sys.h" 

// Mappatura concetti di base
#define LOW  0
#define HIGH 1
#define INPUT GPIO_MODE_INPUT
#define OUTPUT GPIO_MODE_OUTPUT
#define RISING GPIO_INTR_POSEDGE
#define FALLING GPIO_INTR_NEGEDGE

class EspHal : public RadioLibHal {
  public:
    EspHal(spi_host_device_t host, uint32_t spiSpeed, int8_t sck, int8_t miso, int8_t mosi)
      : RadioLibHal(INPUT, OUTPUT, LOW, HIGH, RISING, FALLING),
        spiHost(host), spiSpeed(spiSpeed), spiSCK(sck), spiMISO(miso), spiMOSI(mosi) {}

    void init() override {
        spiBegin();
    }

    void term() override {
        spiEnd();
    }

    void pinMode(uint32_t pin, uint32_t mode) override {
        if(pin == RADIOLIB_NC) return;
        gpio_config_t conf = {};
        conf.pin_bit_mask = (1ULL << pin);
        conf.mode = (gpio_mode_t)mode;
        conf.pull_up_en = GPIO_PULLUP_DISABLE;
        conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
        conf.intr_type = GPIO_INTR_DISABLE;
        gpio_config(&conf);
    }

    void digitalWrite(uint32_t pin, uint32_t value) override {
        if(pin == RADIOLIB_NC) return;
        gpio_set_level((gpio_num_t)pin, value);
    }

    uint32_t digitalRead(uint32_t pin) override {
        if(pin == RADIOLIB_NC) return 0;
        return gpio_get_level((gpio_num_t)pin);
    }

    void attachInterrupt(uint32_t interruptNum, void (*interruptCb)(void), uint32_t mode) override {
        if(interruptNum == RADIOLIB_NC) return;

        // 1. Assicuriamoci che il servizio globale sia avviato, ma senza spammare log di errore
        static bool isr_service_installed = false;
        if (!isr_service_installed) {
            esp_err_t err = gpio_install_isr_service(0);
            if (err == ESP_OK || err == ESP_ERR_INVALID_STATE) {
                isr_service_installed = true;
            }
        }

        // 2. FONDAMENTALE IN ESP-IDF: Rimuoviamo il vecchio "campanello" prima di 
        // metterne uno nuovo, altrimenti RadioLib diventa sordo durante la ricezione!
        gpio_isr_handler_remove((gpio_num_t)interruptNum);
        
        // 3. Assegniamo il nuovo interrupt per la ricezione/trasmissione
        gpio_set_intr_type((gpio_num_t)interruptNum, (gpio_int_type_t)mode);
        gpio_isr_handler_add((gpio_num_t)interruptNum, (gpio_isr_t)interruptCb, NULL);
    }

    void detachInterrupt(uint32_t interruptNum) override {
        if(interruptNum == RADIOLIB_NC) return;
        gpio_isr_handler_remove((gpio_num_t)interruptNum);
        gpio_set_intr_type((gpio_num_t)interruptNum, GPIO_INTR_DISABLE);
    }

    void delay(unsigned long ms) override {
        vTaskDelay(pdMS_TO_TICKS(ms));
    }

    void delayMicroseconds(unsigned long us) override {
        esp_rom_delay_us(us);
    }

    unsigned long millis() override {
        return (unsigned long)(esp_timer_get_time() / 1000ULL);
    }

    unsigned long micros() override {
        return (unsigned long)(esp_timer_get_time());
    }

    long pulseIn(uint32_t pin, uint32_t state, unsigned long timeout) override {
        if(pin == RADIOLIB_NC) return 0;
        this->pinMode(pin, INPUT);
        uint32_t start = this->micros();
        uint32_t curtick = this->micros();
        while(this->digitalRead(pin) == state) {
            if((this->micros() - curtick) > timeout) return 0;
        }
        return (this->micros() - start);
    }

    void spiBegin() {
        spi_bus_config_t buscfg = {};
        buscfg.miso_io_num = spiMISO;
        buscfg.mosi_io_num = spiMOSI;
        buscfg.sclk_io_num = spiSCK;
        buscfg.quadwp_io_num = -1;
        buscfg.quadhd_io_num = -1;
        buscfg.max_transfer_sz = 256;

        spi_device_interface_config_t devcfg = {};
        devcfg.clock_speed_hz = spiSpeed;
        devcfg.mode = 0;
        // Il CS (Chip Select/NSS) viene gestito manualmente da RadioLib
        devcfg.spics_io_num = -1; 
        devcfg.queue_size = 1;

        // Inizializza il bus (ignoriamo l'errore se è già inizializzato)
        spi_bus_initialize(spiHost, &buscfg, SPI_DMA_CH_AUTO);
        spi_bus_add_device(spiHost, &devcfg, &spiHandle);
    }

    void spiBeginTransaction() {}

    uint8_t spiTransferByte(uint8_t b) {
        uint8_t rxData = 0;
        spi_transaction_t t = {};
        t.length = 8;
        t.tx_buffer = &b;
        t.rx_buffer = &rxData;
        spi_device_polling_transmit(spiHandle, &t);
        return rxData;
    }

    void spiTransfer(uint8_t* out, size_t len, uint8_t* in) {
        spi_transaction_t t = {};
        t.length = len * 8; // La lunghezza si esprime in bit!
        t.tx_buffer = out;
        t.rx_buffer = in;
        spi_device_polling_transmit(spiHandle, &t);
    }

    void spiEndTransaction() {}

    void spiEnd() {
        spi_bus_remove_device(spiHandle);
        spi_bus_free(spiHost);
    }

  private:
    spi_host_device_t spiHost;
    spi_device_handle_t spiHandle;
    uint32_t spiSpeed;
    int8_t spiSCK;
    int8_t spiMISO;
    int8_t spiMOSI;
};

#endif