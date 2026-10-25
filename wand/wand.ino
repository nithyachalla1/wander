// Wand firmware: Arduino Nano + Flying-Fish IR sensor + SH1107 1.12" OLED (I2C)
//
// Wiring:
//   IR VCC -> 5V, GND -> GND, DO -> D2
//   OLED VCC -> 3V3, GND -> GND, SCL -> A5, SDA -> A4
//
// Serial protocol (115200 baud, newline-terminated):
//   wand -> PC: "T1" when the IR sensor is covered (casting), "T0" when released
//   PC -> wand: "SHOW:<text>" to display the next spell
//
// Uses U8g2 in page mode (_1_) because a full 128x128 framebuffer would take all 2KB of the Nano's RAM.

#include <U8g2lib.h>
#include <Wire.h>

U8G2_SH1107_128X128_1_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE);
const int IR_PIN = 2;

char spell[24] = "Ready";
bool lastTrig = false;

void setup() {
  Serial.begin(115200);
  pinMode(IR_PIN, INPUT);
  oled.begin();
}

void loop() {
  // Trigger: sensor pulls DO LOW when your thumb covers it
  bool trig = digitalRead(IR_PIN) == LOW;
  if (trig != lastTrig) {
    Serial.println(trig ? "T1" : "T0");
    lastTrig = trig;
  }

  // PC sends lines like "SHOW:Storm Lance"
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    if (line.startsWith("SHOW:")) line.substring(5).toCharArray(spell, sizeof(spell));
  }

  oled.firstPage();
  do {
    oled.setFont(u8g2_font_6x10_tf);
    oled.drawStr(0, 12, "Next spell:");
    oled.setFont(u8g2_font_helvB10_tf);
    oled.drawStr(0, 40, spell);
    oled.drawStr(0, 120, trig ? "CASTING" : "");
  } while (oled.nextPage());
}
