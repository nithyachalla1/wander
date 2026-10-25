// Wand firmware: ideaspark ESP32-WROOM board (built-in 1.9" ST7789 170x320 LCD) + one LED at the tip.
//
// The game turns the wand on when you pick the wand option: the LED lights up for the webcam to follow,
// and the screen shows your spell, health and reactions. When the wand is off the LED is dark, so the
// webcam has nothing to track and nothing gets cast.
//
// Libraries (Arduino Library Manager): "Adafruit ST7735 and ST7789 Library" + "Adafruit GFX Library".
// Board: "ESP32 Dev Module".
//
// Wiring: GPIO25 -> resistor -> LED long leg (+), short leg -> GND.
//         Red/green LED: 100 ohm. White/blue LED: 33 ohm (they need almost all of the 3.3V).
//
// Serial protocol over USB (115200 baud, newline-terminated):
//   wand -> PC: "W" on boot, then "B1" / "B0" (on / off) every second as a heartbeat
//   PC -> wand: "A:1" / "A:0"       wand option selected / deselected: LED and screen on / off
//               "S:<shape>:<name>"  current spell (shape: circle bolt flick spiral up triangle vee star)
//               "H:<0-100>"         player health
//               "M:<text>"          message band ("Good attack!")
//               "O:<text>"          full-screen flash ("Ouch!")
//
// Each frame is drawn into a RAM canvas (170*320*2 = 109KB) and pushed in one go, which takes ~25ms over
// 40MHz SPI and never flickers. If that much RAM isn't free in one piece, it draws straight to the screen.

#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <Fonts/FreeSansBold9pt7b.h>
#include <Fonts/FreeSansBold12pt7b.h>
#include <Fonts/FreeSansBold18pt7b.h>
#include <SPI.h>

// ideaspark 1.9" LCD pins. Check these against your board's listing if the screen stays black.
#define TFT_MOSI 23
#define TFT_SCLK 18
#define TFT_CS 15
#define TFT_DC 2
#define TFT_RST 4
#define TFT_BL 32
#define LED_PIN 25
// 0 = portrait, 2 = portrait upside down (flip it if the screen faces the wrong way on the wand)
#define ROTATION 0

const int W = 170, H = 320;
const unsigned long MSG_MS = 1400, FLASH_MS = 700;

Adafruit_ST7789 tft(TFT_CS, TFT_DC, TFT_RST);
GFXcanvas16 *canvas;
Adafruit_GFX *g;  // what we draw on: the canvas, or the screen itself if the canvas didn't fit in RAM

void centered(const char *s, int baseline);
int textWidth(const char *s);

char spell[24] = "";
char shape[10] = "";
char msg[24] = "";
char flash[16] = "";
int hp = -1;
bool active = false;
unsigned long msgAt = 0, flashAt = 0, beatAt = 0;
bool dirty = true;

char rx[48];
size_t rxLen = 0;

uint16_t rgb(uint32_t hex) { return tft.color565(hex >> 16, (hex >> 8) & 0xff, hex & 0xff); }
uint16_t dimmed(uint32_t hex) { return tft.color565((hex >> 16) / 4, ((hex >> 8) & 0xff) / 4, (hex & 0xff) / 4); }

// Same colors as the spells in the game (wander/spells.py)
uint32_t shapeColor(const char *s) {
  if (!strcmp(s, "circle")) return 0x5ee7ff;
  if (!strcmp(s, "bolt")) return 0xc9a0ff;
  if (!strcmp(s, "flick")) return 0xff7a59;
  if (!strcmp(s, "spiral")) return 0x7dffb0;
  if (!strcmp(s, "up")) return 0xffe27a;
  if (!strcmp(s, "triangle")) return 0x9fd8ff;
  if (!strcmp(s, "vee")) return 0xff9a3c;
  return 0xffd98a;  // star (fusions, forged spells) and the idle screen
}

// ---------------------------------------------------------------- talking to the game
void heartbeat() {
  Serial.println(active ? "B1" : "B0");
  beatAt = millis();
}

void setActive(bool a) {
  if (a != active) dirty = true;
  active = a;
  digitalWrite(LED_PIN, a ? HIGH : LOW);
  heartbeat();
}

bool setField(char *dst, size_t n, const char *src) {
  if (!strncmp(dst, src, n - 1)) return false;  // only redraw on real changes
  strncpy(dst, src, n - 1);
  dst[n - 1] = 0;
  return true;
}

void handleLine(char *line) {
  if (line[0] == 0 || line[1] != ':') return;
  char *arg = line + 2;
  switch (line[0]) {
    case 'A': setActive(arg[0] == '1'); break;
    case 'S': {
      char *sep = strchr(arg, ':');
      if (!sep) return;
      *sep = 0;
      dirty |= setField(shape, sizeof(shape), arg);
      dirty |= setField(spell, sizeof(spell), sep + 1);
      break;
    }
    case 'H': {
      int v = constrain(atoi(arg), 0, 100);
      dirty |= v != hp;
      hp = v;
      break;
    }
    case 'M': setField(msg, sizeof(msg), arg); msgAt = millis(); dirty = true; break;
    case 'O': setField(flash, sizeof(flash), arg); flashAt = millis(); dirty = true; break;
  }
}

void pollSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') { rx[rxLen] = 0; handleLine(rx); rxLen = 0; }
    else if (rxLen < sizeof(rx) - 1) rx[rxLen++] = c;
  }
}

// ---------------------------------------------------------------- drawing helpers
// Round-capped thick line: a row of filled circles. Cheap enough on an ESP32.
void stroke(float x0, float y0, float x1, float y1, int r, uint16_t c) {
  float len = hypot(x1 - x0, y1 - y0);
  int n = max(1, (int)(len / 2));
  for (int i = 0; i <= n; i++) g->fillCircle(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n, r, c);
}

// Shapes are laid out in a -1..1 box, then scaled by R around (cx, cy).
// Each is drawn twice: a wide dim pass for glow, then a thin bright core.
void drawShapeLines(const char *s, float cx, float cy, float R, uint32_t hex, bool glow) {
  int r = glow ? 6 : 2;
  uint16_t c = glow ? dimmed(hex) : rgb(hex);
  auto L = [&](float ax, float ay, float bx, float by) { stroke(cx + ax * R, cy + ay * R, cx + bx * R, cy + by * R, r, c); };
  auto poly = [&](float (*f)(int, bool), int n) {  // f(i, isY) gives point i of an n-step curve
    for (int i = 0; i < n; i++) L(f(i, false), f(i, true), f(i + 1, false), f(i + 1, true));
  };
  if (!strcmp(s, "circle")) {
    poly([](int i, bool y) { float a = i * TWO_PI / 48; return y ? sinf(a) * 0.9f : cosf(a) * 0.9f; }, 48);
  } else if (!strcmp(s, "bolt")) {
    L(0.4, -1, -0.4, -0.1); L(-0.4, -0.1, 0.4, 0.1); L(0.4, 0.1, -0.4, 1);
  } else if (!strcmp(s, "flick")) {
    L(-0.85, 0.85, 0.85, -0.85); L(0.85, -0.85, 0.45, -0.85); L(0.85, -0.85, 0.85, -0.45);
  } else if (!strcmp(s, "spiral")) {
    poly([](int i, bool y) { float a = i * 0.21f, r = i * 0.016f; return y ? sinf(a) * r : cosf(a) * r; }, 60);
  } else if (!strcmp(s, "up")) {
    L(0, 1, 0, -1); L(0, -1, -0.35, -0.6); L(0, -1, 0.35, -0.6);
  } else if (!strcmp(s, "triangle")) {
    L(0, -0.9, 0.9, 0.75); L(0.9, 0.75, -0.9, 0.75); L(-0.9, 0.75, 0, -0.9);
  } else if (!strcmp(s, "vee")) {
    L(-0.85, -0.85, 0, 0.85); L(0, 0.85, 0.85, -0.85);
  } else {  // star
    for (int i = 0; i < 8; i++) {
      float a = i * PI / 4, rr = (i % 2) ? 0.55f : 1.0f;
      L(0, 0, cosf(a) * rr, sinf(a) * rr);
    }
  }
}

void drawGlyph(const char *s, float cx, float cy, float R) {
  uint32_t hex = shapeColor(s);
  if (!s[0]) {  // nothing cast yet
    g->setFont(&FreeSansBold12pt7b);
    g->setTextColor(rgb(hex));
    centered("let's go!", cy + 8);
    return;
  }
  drawShapeLines(s, cx, cy, R, hex, true);
  drawShapeLines(s, cx, cy, R, hex, false);
}

int textWidth(const char *s) {
  int16_t x, y;
  uint16_t w, h;
  g->getTextBounds(s, 0, 0, &x, &y, &w, &h);
  return w;
}

void centered(const char *s, int baseline) {
  g->setCursor((W - textWidth(s)) / 2, baseline);
  g->print(s);
}

// Biggest of the given fonts that fits the width
void fitFont(const char *s, int maxW, const GFXfont *big, const GFXfont *small) {
  g->setFont(big);
  if (textWidth(s) > maxW) g->setFont(small);
}

// ---------------------------------------------------------------- screens
// Wand option not selected (or the game isn't running): a dim title card, LED off.
void drawIdle() {
  g->setFont(&FreeSansBold18pt7b);
  g->setTextColor(rgb(0xffd98a));
  centered("WANDER", 130);
  g->setFont(&FreeSansBold9pt7b);
  g->setTextColor(rgb(0xa99bc4));
  centered("Pick the wand", 180);
  centered("option in the game", 202);
  drawShapeLines("star", W / 2, 260, 26, 0x6d6185, true);
}

void drawFrame() {
  unsigned long t = millis();
  g->fillScreen(0x0000);

  if (flash[0] && t - flashAt < FLASH_MS) {
    bool win = !strcmp(flash, "VICTORY");
    g->fillScreen(win ? rgb(0xffd98a) : rgb(0xd0103a));
    g->setTextColor(win ? 0x0000 : 0xffff);
    fitFont(flash, W - 12, &FreeSansBold18pt7b, &FreeSansBold12pt7b);
    centered(flash, H / 2 + 12);
    return;
  }

  if (!active) {
    drawIdle();
    return;
  }

  // "ON" pill
  g->setFont(&FreeSansBold9pt7b);
  g->fillRoundRect(8, 8, 60, 26, 13, rgb(0x7dffb0));
  g->setTextColor(0x0000);
  g->setCursor(8 + (60 - textWidth("ON")) / 2, 27);
  g->print("ON");

  // Health bar, green -> red as it drops
  if (hp >= 0) {
    g->setTextColor(rgb(0xa99bc4));
    g->setCursor(W - 8 - textWidth("HP"), 27);
    g->print("HP");
    g->drawRoundRect(8, 42, W - 16, 14, 7, rgb(0x6d6185));
    uint32_t c = hp > 50 ? 0x7dffb0 : hp > 25 ? 0xffe27a : 0xff5577;
    int w = (W - 20) * hp / 100;
    if (w > 0) g->fillRoundRect(10, 44, w, 10, 5, rgb(c));
  }

  drawGlyph(shape, W / 2, 140, 62);

  g->setTextColor(rgb(shapeColor(shape)));
  const char *name = spell[0] ? spell : "Draw a spell";
  fitFont(name, W - 8, &FreeSansBold12pt7b, &FreeSansBold9pt7b);
  centered(name, 240);

  if (msg[0] && t - msgAt < MSG_MS) {
    g->fillRoundRect(6, 262, W - 12, 50, 12, rgb(0xffd98a));
    g->setTextColor(0x0000);
    fitFont(msg, W - 24, &FreeSansBold12pt7b, &FreeSansBold9pt7b);
    centered(msg, 295);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);
  SPI.begin(TFT_SCLK, -1, TFT_MOSI, TFT_CS);
  tft.init(170, 320);  // if the screen stays black or garbled, try tft.init(170, 320, SPI_MODE3)
  tft.setSPISpeed(40000000);
  tft.setRotation(ROTATION);
  canvas = new GFXcanvas16(W, H);
  if (!canvas->getBuffer()) {
    delete canvas;
    canvas = nullptr;
  }
  g = canvas ? (Adafruit_GFX *)canvas : (Adafruit_GFX *)&tft;
  Serial.println("W");  // "I just booted": opening the port resets the board, so the game re-sends its state
  heartbeat();
}

void loop() {
  pollSerial();

  unsigned long t = millis();
  if (t - beatAt > 1000) heartbeat();

  // Redraw once more when a message or flash expires
  static bool hadMsg = false, hadFlash = false;
  bool hasMsg = msg[0] && t - msgAt < MSG_MS, hasFlash = flash[0] && t - flashAt < FLASH_MS;
  if (hasMsg != hadMsg || hasFlash != hadFlash) dirty = true;
  hadMsg = hasMsg;
  hadFlash = hasFlash;

  if (!dirty) return;
  dirty = false;
  drawFrame();
  if (canvas) tft.drawRGBBitmap(0, 0, canvas->getBuffer(), W, H);
}
