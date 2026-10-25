// Wand firmware: Arduino Nano + Flying-Fish IR module (beacon at the tip) + button + SH1107 1.12" OLED (I2C)
//
// The webcam tracks the IR module's emitter LED as a bright dot, so the module is only a light here;
// its DO pin is unused. The button arms/disarms the wand so waving it around never casts by accident.
//
// Wiring:
//   IR VCC -> 5V (or D4, see IR_POWER_PIN), GND -> GND, DO -> not connected
//   Button: D3 -> button -> GND (internal pull-up, no resistor needed)
//   OLED VCC -> 3V3, GND -> GND, SCL -> A5, SDA -> A4
//
// Serial protocol (115200 baud, newline-terminated):
//   wand -> PC: "W" on boot, "B1" / "B0" when armed / disarmed (repeated every second as a heartbeat)
//   PC -> wand: "S:<shape>:<name>"  current spell (shape: circle bolt flick spiral up triangle vee star)
//               "H:<0-100>"         player health
//               "M:<text>"          short message in the bottom band ("Good attack!")
//               "O:<text>"          full-screen inverted flash ("Ouch!")
//
// U8g2 page mode (_1_) because a full 128x128 framebuffer would take all 2KB of the Nano's RAM.
// A frame takes ~60ms at 400kHz I2C, so we only redraw when something changed, and keep polling the
// button and serial between pages so a redraw never drops input.

#include <U8g2lib.h>
#include <Wire.h>

// 1 = momentary push button, each press toggles armed. 0 = latching switch / hold-to-cast (armed while closed).
#define BUTTON_TOGGLES 1
// Wire IR VCC to this pin instead of 5V to turn the beacon off when disarmed (module draws ~25mA, fine for a pin).
// -1 = beacon always on (lets the game show your aim even while disarmed).
#define IR_POWER_PIN -1

U8G2_SH1107_128X128_1_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE);
const int BUTTON_PIN = 3;
const unsigned long MSG_MS = 1400, FLASH_MS = 700;

char spell[20] = "";
char shape[10] = "";
char msg[20] = "";
char flash[12] = "";
int hp = -1;
bool armed = false;
unsigned long msgAt = 0, flashAt = 0, beatAt = 0;
bool dirty = true;

char rx[40];
byte rxLen = 0;

bool btnRaw = false, btnStable = false;
unsigned long btnAt = 0;

void setArmed(bool a) {
  armed = a;
  if (IR_POWER_PIN >= 0) digitalWrite(IR_POWER_PIN, a ? HIGH : LOW);
  Serial.println(a ? F("B1") : F("B0"));
  beatAt = millis();
  dirty = true;
}

void pollButton() {
  bool down = digitalRead(BUTTON_PIN) == LOW;
  unsigned long t = millis();
  if (down != btnRaw) { btnRaw = down; btnAt = t; }
  if (btnRaw != btnStable && t - btnAt > 25) {
    btnStable = btnRaw;
    if (BUTTON_TOGGLES) { if (btnStable) setArmed(!armed); }
    else setArmed(btnStable);
  }
}

void copyField(char *dst, size_t n, const char *src) {
  strncpy(dst, src, n - 1);
  dst[n - 1] = 0;
}

void handleLine(char *line) {
  if (line[0] == 0 || line[1] != ':') return;
  char *arg = line + 2;
  switch (line[0]) {
    case 'S': {
      char *sep = strchr(arg, ':');
      if (!sep) return;
      *sep = 0;
      copyField(shape, sizeof(shape), arg);
      copyField(spell, sizeof(spell), sep + 1);
      break;
    }
    case 'H': hp = constrain(atoi(arg), 0, 100); break;
    case 'M': copyField(msg, sizeof(msg), arg); msgAt = millis(); break;
    case 'O': copyField(flash, sizeof(flash), arg); flashAt = millis(); break;
    default: return;
  }
  dirty = true;
}

void pollSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') { rx[rxLen] = 0; handleLine(rx); rxLen = 0; }
    else if (rxLen < sizeof(rx) - 1) rx[rxLen++] = c;
  }
}

void poll() {
  pollButton();
  pollSerial();
}

// ---------------------------------------------------------------- spell glyphs (in a 56x56 box)
void thickLine(int x0, int y0, int x1, int y1) {
  oled.drawLine(x0, y0, x1, y1);
  oled.drawLine(x0 + 1, y0, x1 + 1, y1);
  oled.drawLine(x0, y0 + 1, x1, y1 + 1);
}

void arrowHead(int x, int y, int dx, int dy) {  // tip at (x,y), pointing along (dx,dy) in {-1,0,1}
  thickLine(x, y, x - 7 * dx + 5 * dy, y - 7 * dy - 5 * dx);
  thickLine(x, y, x - 7 * dx - 5 * dy, y - 7 * dy + 5 * dx);
}

void drawGlyph(const char *s, int cx, int cy) {
  if (!strcmp(s, "circle")) {
    oled.drawCircle(cx, cy, 24);
    oled.drawCircle(cx, cy, 23);
  } else if (!strcmp(s, "bolt")) {
    thickLine(cx + 10, cy - 26, cx - 10, cy - 2);
    thickLine(cx - 10, cy - 2, cx + 10, cy + 2);
    thickLine(cx + 10, cy + 2, cx - 10, cy + 26);
  } else if (!strcmp(s, "flick")) {
    thickLine(cx - 22, cy + 22, cx + 22, cy - 22);
    thickLine(cx + 22, cy - 22, cx + 12, cy - 22);
    thickLine(cx + 22, cy - 22, cx + 22, cy - 12);
  } else if (!strcmp(s, "spiral")) {
    float px = cx, py = cy;
    for (int i = 1; i <= 60; i++) {
      float a = i * 0.21f, r = i * 0.42f;
      float x = cx + cos(a) * r, y = cy + sin(a) * r;
      thickLine(px, py, x, y);
      px = x; py = y;
    }
  } else if (!strcmp(s, "up")) {
    thickLine(cx, cy + 26, cx, cy - 26);
    arrowHead(cx, cy - 26, 0, -1);
  } else if (!strcmp(s, "triangle")) {
    thickLine(cx, cy - 24, cx + 24, cy + 20);
    thickLine(cx + 24, cy + 20, cx - 24, cy + 20);
    thickLine(cx - 24, cy + 20, cx, cy - 24);
  } else if (!strcmp(s, "vee")) {
    thickLine(cx - 22, cy - 22, cx, cy + 22);
    thickLine(cx, cy + 22, cx + 22, cy - 22);
  } else if (!strcmp(s, "star")) {  // fusions and forged spells
    for (int i = 0; i < 8; i++) {
      float a = i * PI / 4;
      int r = (i % 2) ? 14 : 26;
      thickLine(cx, cy, cx + cos(a) * r, cy + sin(a) * r);
    }
  } else {  // nothing cast yet
    oled.setFont(u8g2_font_helvB14_tr);
    const char *t = "let's go!";
    oled.drawStr(cx - oled.getStrWidth(t) / 2, cy + 7, t);
  }
}

void centered(const char *s, int y) {
  oled.drawStr((128 - oled.getStrWidth(s)) / 2, y, s);
}

// Names from the game can be up to 19 chars (forged spells), wider than 128px in helvB10:
// fall back to the small font rather than running off the screen.
void centeredFit(const char *s, int y) {
  oled.setFont(u8g2_font_helvB10_tr);
  if (oled.getStrWidth(s) > 124) oled.setFont(u8g2_font_6x10_tr);
  centered(s, y);
}

void drawFrame() {
  unsigned long t = millis();
  bool showFlash = flash[0] && t - flashAt < FLASH_MS;
  bool showMsg = msg[0] && t - msgAt < MSG_MS;

  if (showFlash) {
    oled.drawBox(0, 0, 128, 128);
    oled.setDrawColor(0);
    oled.setFont(u8g2_font_helvB18_tr);
    centered(flash, 72);
    oled.setDrawColor(1);
    return;
  }

  // Top bar: armed state + health
  oled.setFont(u8g2_font_6x10_tr);
  if (armed) {
    oled.drawBox(0, 0, 36, 11);
    oled.setDrawColor(0);
    oled.drawStr(3, 9, "ARMED");
    oled.setDrawColor(1);
  } else {
    oled.drawFrame(0, 0, 30, 11);
    oled.drawStr(6, 9, "OFF");
  }
  if (hp >= 0) {
    oled.drawFrame(44, 2, 84, 7);
    oled.drawBox(46, 4, hp * 80 / 100, 3);
  }

  drawGlyph(shape, 64, 50);

  centeredFit(spell[0] ? spell : "Draw a spell", 98);

  if (showMsg) {
    oled.drawBox(0, 106, 128, 22);
    oled.setDrawColor(0);
    centeredFit(msg, 122);
    oled.setDrawColor(1);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  if (IR_POWER_PIN >= 0) pinMode(IR_POWER_PIN, OUTPUT);
  oled.setBusClock(400000);  // must come before begin()
  oled.begin();
  Serial.println(F("W"));
  setArmed(BUTTON_TOGGLES ? false : digitalRead(BUTTON_PIN) == LOW);
}

void loop() {
  poll();

  unsigned long t = millis();
  if (t - beatAt > 1000) {
    Serial.println(armed ? F("B1") : F("B0"));
    beatAt = t;
  }
  // Redraw once more when a message or flash expires
  static bool hadMsg = false, hadFlash = false;
  bool hasMsg = msg[0] && t - msgAt < MSG_MS, hasFlash = flash[0] && t - flashAt < FLASH_MS;
  if (hasMsg != hadMsg || hasFlash != hadFlash) dirty = true;
  hadMsg = hasMsg;
  hadFlash = hasFlash;

  if (!dirty) return;
  dirty = false;
  oled.firstPage();
  do {
    drawFrame();
    poll();
  } while (oled.nextPage());
}
