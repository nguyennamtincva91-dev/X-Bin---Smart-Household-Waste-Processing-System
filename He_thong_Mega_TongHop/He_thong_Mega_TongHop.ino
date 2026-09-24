#include "HX711.h"

// ======================================================
// HE THONG TICH HOP KHONG CHAN (NON-BLOCKING)
// - Phan cua: E18-D80NK + motor cua A4988/DRV8825
// - Phan ep: Loadcell 200 kg + HX711 + motor ep + limit switch
//
// Muc tieu:
// Motor cua dang mo/dong thi motor ep van tiep tuc chay va doc luc.
// Khong dung vong for quay motor va khong delay() khi van hanh.
// ======================================================


// ======================================================
// PHAN 1: MOTOR CUA + CAM BIEN QUANG E18-D80NK
// ======================================================
#define DOOR_STEP_PIN    30
#define DOOR_DIR_PIN     31
#define SENSOR_PIN       33

// E18-D80NK loai NPN: phat hien vat thi keo tin hieu xuong LOW.
#define SENSOR_DETECTED  LOW

// Driver cua dang de dat 1/8 microstep:
// Motor 200 buoc/vong -> 200 * 8 / 4 = 400 xung cho 90 do.
const long DOOR_STEPS_90 = 50;


// HIGH 1000 us + LOW 1000 us -> 500 xung/giay.
const unsigned long DOOR_STEP_HALF_PERIOD_US = 1000UL;

const unsigned long DETECT_TIME_MS = 5000UL;
const unsigned long RETURN_TIME_MS = 10000UL;

const int DOOR_DIR_OPEN  = HIGH;
const int DOOR_DIR_CLOSE = LOW;

enum DoorMotionState {
  DOOR_STOPPED,
  DOOR_OPENING,
  DOOR_CLOSING
};

DoorMotionState doorMotion = DOOR_STOPPED;

bool doorOpened = false;                  // true khi cua da mo xong
unsigned long detectStartTime = 0;
unsigned long doorOpenedTime = 0;

// ===== AUTO MODE (host Q509 dieu khien qua lenh A/M) =====
// Khi autoModeEnabled = true:
//   - Sensor 5s lien tuc -> KHONG tu mo cua, gui "AUTO_TRIGGER" cho host.
//   - Host orchestrate cycle: cat -> bio -> mo cua 5s -> dong.
// Khi false (default): manual mode, sensor khong tu lam gi voi cua.
bool autoModeEnabled = false;
unsigned long lastAutoTriggerMs = 0;
const unsigned long AUTO_RETRIGGER_GUARD_MS = 30000UL;

// Track edge cua sensor de gui SENSOR_DETECT/CLEAR cho host (UI realtime).
bool lastSensorReported = false;

// Co: khi vua bat Auto ma sensor dang DETECT (boot/tay con tren bien)
// -> phai cho CLEAR roi moi bat dau dem. Tranh tu trigger lan dau.
bool waitForSensorClear = false;

// Debounce CLEAR: E18 hay jitter (detect <-> clear chop nhoang)
// Nen khi sensor "clear", cho 300ms truoc khi reset bo dem 5s.
// Tranh truong hop dat tay yen ma bo dem reset lien tuc, phai cho >5s moi trigger.
unsigned long sensorClearStartMs = 0;
const unsigned long SENSOR_CLEAR_DEBOUNCE_MS = 300UL;

long doorStepsRemaining = 0;
bool doorPulseHigh = false;
unsigned long doorNextEdgeUs = 0;


// ======================================================
// PHAN 3: HAI CAM BIEN MUC DUNG DICH XKC-Y25-PNP
// ======================================================
// Da test theo cach dau:
// - Cam bien binh chat tay rua: day OUT -> D34
// - Cam bien binh vi sinh:    day OUT -> D35
// - Ban PNP: tai vi tri cam bien CO dung dich -> HIGH
// - Moi chan OUT co dien tro keo xuong GND (vi du 10 kOhm).
// Khong dung INPUT_PULLUP cho hai chan nay.
#define DETERGENT_LEVEL_PIN  34
#define MICROBE_LEVEL_PIN    35
#define LIQUID_PRESENT       HIGH

// Yeu cau trang thai lien tuc 3 giay moi doi canh bao,
// tranh dung dich song sanh quanh vi tri 20%.
const unsigned long LIQUID_CONFIRM_TIME_MS = 3000UL;

struct LiquidTankStatus {
  byte pin;
  const char* name;
  const char* code;
  bool candidateHasLiquid;
  bool stableHasLiquid;
  bool stableKnown;
  unsigned long candidateSince;
};

LiquidTankStatus detergentTank = {
  DETERGENT_LEVEL_PIN, "Binh chat tay rua", "DETERGENT",
  false, false, false, 0
};

LiquidTankStatus microbeTank = {
  MICROBE_LEVEL_PIN, "Binh vi sinh", "MICROBE",
  false, false, false, 0
};


// ======================================================
// PHAN 4: MOTOR CAT DC 775 + BTS7960
// ======================================================
// BTS7960:
//   RPWM -> D44 (PWM)
//   LPWM -> D45 (PWM)
//   R_EN -> D46
//   L_EN -> D47
//
// Dieu khien Serial:
//   1 / 3 / 5 : chon thoi gian cat theo phut
//   y         : bat dau cat theo thoi gian da chon
//   z         : dung motor cat ngay
//
// D44, D45 la hai chan PWM con trong tren Mega.
// Motor chay mot chieu: RPWM co PWM, LPWM = 0.
#define CUTTER_RPWM_PIN 44
#define CUTTER_LPWM_PIN 45
#define CUTTER_REN_PIN  46
#define CUTTER_LEN_PIN  47

// PWM tu 0 den 255. Gia tri 180 de bat dau an toan hon;
// khi co khi da on dinh va can them luc cat, co the tang dan.
const byte CUTTER_PWM_TARGET = 180;

// Tang toc mem de han che dong khoi dong dot ngot.
const byte CUTTER_RAMP_STEP = 5;
const unsigned long CUTTER_RAMP_INTERVAL_MS = 30UL;

byte selectedCutMinutes = 1;
unsigned long selectedCutDurationMs = 60000UL;

bool cutterRunning = false;
byte cutterCurrentPwm = 0;
unsigned long cutterStartMs = 0;
unsigned long cutterLastRampMs = 0;
unsigned long cutterLastReportMs = 0;


// ======================================================
// PHAN 5: HAI BOM DUNG DICH + MODULE 2 RELAY 5V
// ======================================================
// Relay da test voi jumper:
//   S1 = COM-HIGH, S2 = COM-HIGH
// Nen HIGH = bat relay, LOW = tat relay.
//
// Dau noi:
//   D36 -> IN1: bom chat tay rua (khoang vo co)
//   D37 -> IN2: bom vi sinh (khoang huu co)
#define DETERGENT_PUMP_RELAY_PIN  36
#define MICROBE_PUMP_RELAY_PIN    37

#define RELAY_ON   HIGH
#define RELAY_OFF  LOW

// THOI GIAN PHUN TEST TAM THOI = 3 giay.
// Chua du du lieu de xac dinh lieu phun thuc te.
// Sau khi do luong ml/3s, thay hai gia tri nay.
const unsigned long DETERGENT_SPRAY_TIME_MS = 3000UL;
const unsigned long MICROBE_SPRAY_TIME_MS   = 3000UL;

enum PumpState {
  PUMPS_IDLE,
  DETERGENT_PUMP_RUNNING,
  MICROBE_PUMP_RUNNING
};

PumpState pumpState = PUMPS_IDLE;
unsigned long pumpStartMs = 0;

bool detergentSprayRequested = false;
bool microbeSprayRequested = false;

// (Da bo cac bien organicCycleStarted / organicCutterFinished / organicDoorFinished —
//  host Q509 orchestrate chu ky huu co qua lenh serial nen Mega khong tu trigger nua.)


// ======================================================
// PHAN 6: HAI CAM BIEN SIEU AM JSN-SR04T BAO DAY THUNG RAC
// ======================================================

//   - Cach ly chan (dua TAT CA chan JSN ve INPUT) truoc khi kich tung
//     cam bien -> 2 module cam dong thoi khong nhieu nhau.
//   - Xung TRIG 50us (JSN-SR04T can xung dai hon HC-SR04 10us).
//   - Dung pulseIn (chan) NHUNG chi doc khi may dang RANH (khong doc
//     luc motor/bom chay) -> khong lam giat xung step.
//   - Loc mau bat thuong: < 20 cm (vung mu) hoac > 450 cm -> bo.
//   - Co tre xac nhan + hysteresis -> tranh bao day/het ao.
//
// Thung vo co:  RX/TRIG -> D39 | TX/ECHO -> D38
// Thung huu co: RX/TRIG -> D41 | TX/ECHO -> D40
#define INORG_BIN_TRIG_PIN 39
#define INORG_BIN_ECHO_PIN 38
#define ORG_BIN_TRIG_PIN   41
#define ORG_BIN_ECHO_PIN   40

// ----- NGUONG (tinh chinh theo thuc te khi gan len thung) -----
// FULL (UI=HIGH) khi khoang cach <= fullDistanceCm.
// Tro lai OK (UI=LOW) khi >= clearDistanceCm.
// LUU Y: JSN-SR04T doc khong dang tin duoi ~20 cm (vung mu) -> fullDistanceCm
// nen >= 25 cm. clearDistanceCm > fullDistanceCm de chong rung trang thai.
const float INORG_BIN_FULL_DISTANCE_CM  = 25.0;
const float INORG_BIN_CLEAR_DISTANCE_CM = 30.0;
const float ORG_BIN_FULL_DISTANCE_CM    = 25.0;
const float ORG_BIN_CLEAR_DISTANCE_CM   = 30.0;

// Giu trang thai lien tuc bao lau moi doi (chong nhieu/rac loi lom).
const unsigned long TRASH_BIN_CONFIRM_TIME_MS = 2000UL;

// Khoang cach giua 2 lan doc. Doc LUAN PHIEN 2 cam bien nen
// 1000ms -> moi cam bien duoc do lai ~2 giay (theo yeu cau).
const unsigned long TRASH_BIN_READ_INTERVAL_MS = 1000UL;

struct TrashBinStatus {
  byte trigPin;
  byte echoPin;
  const char* name;
  const char* code;            // "INORGANIC_BIN"/"ORGANIC_BIN" 
  float fullDistanceCm;
  float clearDistanceCm;
  bool full;
  bool known;
  float lastDistanceCm;        
  unsigned long fullStartMs;
  unsigned long clearStartMs;
};

TrashBinStatus inorganicTrashBin = {
  INORG_BIN_TRIG_PIN, INORG_BIN_ECHO_PIN,
  "Thung rac vo co", "INORGANIC_BIN",
  INORG_BIN_FULL_DISTANCE_CM, INORG_BIN_CLEAR_DISTANCE_CM,
  false, false, -1.0, 0, 0
};

TrashBinStatus organicTrashBin = {
  ORG_BIN_TRIG_PIN, ORG_BIN_ECHO_PIN,
  "Thung rac huu co", "ORGANIC_BIN",
  ORG_BIN_FULL_DISTANCE_CM, ORG_BIN_CLEAR_DISTANCE_CM,
  false, false, -1.0, 0, 0
};

unsigned long lastTrashBinReadMs = 0;
bool nextReadInorganicBin = true;


// ======================================================
// PHAN 2: MOTOR EP + LOADCELL 200 KG + CONG TAC HANH TRINH
// ======================================================

// HX711
#define HX711_DT_PIN     3
#define HX711_SCK_PIN    2

// Driver motor ep
#define PRESS_PUL_PIN    8
#define PRESS_DIR_PIN    9

// Cong tac hanh trinh:
// COM -> GND
// NO  -> D22
#define LIMIT_PIN        22

HX711 scale;

// Loadcell 200 kg: da test bang dong co NEMA23 xap xi 1.72 kg.
float calibration_factor = -19.60;

// Nguong TEST: dat NEMA23 len loadcell se vuot nguong va dao chieu motor ep.
float force_limit_g = 1500.0;

// Khi motor chay, neu so do luc rung nhieu hon thi tang len 100 hoac 200 g.
float dead_zone_g = 30.0;

int over_count = 0;
const int required_count = 5;


// HIGH 800 us + LOW 800 us.
const unsigned long PRESS_STEP_HALF_PERIOD_US = 800UL;

const int PRESS_DIR_DOWN = HIGH;
const int PRESS_DIR_UP   = LOW;

enum PressState {
  PRESS_IDLE,
  PRESS_TARE_WAIT,
  PRESS_TARE_COLLECT,
  PRESSING,
  PRESS_REVERSE_WAIT,
  REVERSING,
  PRESS_DONE
};

PressState pressState = PRESS_IDLE;

bool pressMotorRunning = false;
bool pressPulseHigh = false;
unsigned long pressNextEdgeUs = 0;

unsigned long pressStateStartTime = 0;
unsigned long lastPrintTime = 0;
float currentForce = 0;

// Tare khong chan: cho 2 giay de bo tay khoi loadcell,
// sau do lay 20 mau rieng le ma van cho phep motor cua hoat dong.
const unsigned long TARE_WAIT_MS = 2000UL;
const byte TARE_SAMPLE_TARGET = 20;
const unsigned long TARE_COLLECT_TIMEOUT_MS = 6000UL;
long tareRawSum = 0;
byte tareSampleCount = 0;


// ======================================================
// THOI GIAN ON DINH CHIEU QUAY CHO DRIVER
// ======================================================
const unsigned long DIR_SETTLE_TIME_US = 5000UL;


// ======================================================
// KHAI BAO HAM
// ======================================================
void handleSerialCommand();
void handleDoorSystem();
void updateDoorMotor();
void startDoorOpening();
void startDoorClosing();
void startDoorMove(DoorMotionState motion, int direction, long steps);
void finishDoorMove();
bool isDoorMoving();

void handleLiquidLevelSystem();
void initializeLiquidTank(LiquidTankStatus &tank);
void updateLiquidTank(LiquidTankStatus &tank);
void printLiquidTankStatus(const LiquidTankStatus &tank);
void printAllLiquidLevels();

void handleCutterSystem();
void setCuttingTime(byte minutes);
void startCutterMotor();
void stopCutterMotor(const char* reason);

void handlePumpSystem();
void requestDetergentSpray(const char* reason);
void requestMicrobeSpray(const char* reason);
bool canUseTankForSpray(const LiquidTankStatus &tank);
void startDetergentSpray();
void startMicrobeSpray();
void stopAllPumps(const char* reason);

void startPressCycle();
void resetPressSystem();
void handlePressSystem();
void updatePressMotor();
void startPressMotor(int direction);
void stopPressMotor();
bool isLimitPressed();
float readForceReady();

void initializeTrashBinSystem();
void isolateAllJSNPins();
bool canReadTrashBinsNow();
void handleTrashBinSystem();
float readTrashDistanceOnce(TrashBinStatus &bin, TrashBinStatus &otherBin);
void updateTrashBinStatus(TrashBinStatus &bin, float distanceCm);
void printTrashBinStatus(const TrashBinStatus &bin);
void printAllTrashBinStatus();

bool timeReachedUs(unsigned long nowUs, unsigned long targetUs);


// ======================================================
// SETUP CHUNG
// ======================================================
void setup() {
  Serial.begin(9600);

  // ----- Phan cua -----
  pinMode(DOOR_STEP_PIN, OUTPUT);
  pinMode(DOOR_DIR_PIN, OUTPUT);
  pinMode(SENSOR_PIN, INPUT_PULLUP);

  digitalWrite(DOOR_STEP_PIN, LOW);
  digitalWrite(DOOR_DIR_PIN, DOOR_DIR_OPEN);

  // ----- Phan ep -----
  pinMode(PRESS_PUL_PIN, OUTPUT);
  pinMode(PRESS_DIR_PIN, OUTPUT);
  pinMode(LIMIT_PIN, INPUT_PULLUP);

  digitalWrite(PRESS_PUL_PIN, LOW);
  digitalWrite(PRESS_DIR_PIN, PRESS_DIR_DOWN);

  scale.begin(HX711_DT_PIN, HX711_SCK_PIN);
  scale.set_scale(calibration_factor);

  // ----- Phan bao muc dung dich -----
  // XKC-Y25-PNP da mac dien tro pull-down ngoai o D34 va D35.
  pinMode(DETERGENT_LEVEL_PIN, INPUT);
  pinMode(MICROBE_LEVEL_PIN, INPUT);
  initializeLiquidTank(detergentTank);
  initializeLiquidTank(microbeTank);

  // ----- Phan motor cat DC 775 + BTS7960 -----
  pinMode(CUTTER_RPWM_PIN, OUTPUT);
  pinMode(CUTTER_LPWM_PIN, OUTPUT);
  pinMode(CUTTER_REN_PIN, OUTPUT);
  pinMode(CUTTER_LEN_PIN, OUTPUT);

  analogWrite(CUTTER_RPWM_PIN, 0);
  analogWrite(CUTTER_LPWM_PIN, 0);
  digitalWrite(CUTTER_REN_PIN, LOW);
  digitalWrite(CUTTER_LEN_PIN, LOW);

  // ----- Phan hai bom dung dich + relay -----
  // Dat OFF truoc khi chuyen pin sang OUTPUT de tranh bom bat khi khoi dong.
  digitalWrite(DETERGENT_PUMP_RELAY_PIN, RELAY_OFF);
  digitalWrite(MICROBE_PUMP_RELAY_PIN, RELAY_OFF);
  pinMode(DETERGENT_PUMP_RELAY_PIN, OUTPUT);
  pinMode(MICROBE_PUMP_RELAY_PIN, OUTPUT);
  digitalWrite(DETERGENT_PUMP_RELAY_PIN, RELAY_OFF);
  digitalWrite(MICROBE_PUMP_RELAY_PIN, RELAY_OFF);

  // ----- Phan 6: hai cam bien sieu am JSN-SR04T bao day thung rac -----
  initializeTrashBinSystem();

  Serial.println("=== HE THONG MEGA: NON-BLOCKING + LEVEL + MOTOR CAT + 2 BOM ===");
  Serial.println("CHE DO AUTO (host Q509): A bat | M tat");
  Serial.println("PHAN CUA + CAM BIEN QUANG:");
  Serial.println("x: mo cua 90 do | g: dong cua");
  Serial.println("Sensor E18 hoat dong logic 5s CHI khi auto bat (lenh A)");
  Serial.println("--------------------------------------");
  Serial.println("PHAN EP + LOADCELL:");
  Serial.println("s: bat dau chu ky ep | r: dung/reset ve IDLE");
  Serial.print("Loadcell 200 kg | calibration_factor = ");
  Serial.println(calibration_factor, 2);
  Serial.print("Nguong dao chieu TEST = ");
  Serial.print(force_limit_g, 1);
  Serial.println(" g");
  Serial.println("--------------------------------------");
  Serial.println("PHAN MUC DUNG DICH XKC-Y25-PNP:");
  Serial.println("D34: binh chat tay rua | D35: binh vi sinh");
  Serial.println("Duoi muc 20% lien tuc 3s -> gui canh bao.");
  Serial.println("l: xem trang thai muc dung dich hien tai");
  Serial.println("u: doc muc day 2 thung rac JSN-SR04T (<=25cm=FULL/HIGH)");
  Serial.println("--------------------------------------");
  Serial.println("PHAN MOTOR CAT DC 775 + BTS7960:");
  Serial.println("1 / 3 / 5: chon thoi gian cat (phut)");
  Serial.println("y: bat dau cat | z: dung motor cat ngay");
  Serial.println("Mac dinh: 1 phut | PWM bat dau: 180/255");
  Serial.println("--------------------------------------");
  Serial.println("PHAN BOM DUNG DICH + RELAY:");
  Serial.println("D36/IN1: bom chat tay rua | D37/IN2: bom vi sinh");
  Serial.println("Ep cham limit -> bom chat tay rua 3s (TEST)");
  Serial.println("Cat ket thuc + cua da dong -> bom vi sinh 3s (TEST)");
  Serial.println("t: test bom tay rua | v: test bom vi sinh | q: dung ca hai bom");
  Serial.println("--------------------------------------");
  Serial.println("Cac co cau hoat dong theo kieu non-blocking.");
}


// ======================================================
// LOOP CHUNG
// ======================================================
void loop() {
  // Cap xung cho hai motor truoc tien de giu chuyen dong deu.
  updateDoorMotor();
  updatePressMotor();

  handleSerialCommand();
  handleDoorSystem();
  handlePressSystem();
  handleLiquidLevelSystem();
  handleCutterSystem();
  handlePumpSystem();
  handleTrashBinSystem();

  // Goi lai sau xu ly cam bien/HX711 de giam do tre cap xung.
  updateDoorMotor();
  updatePressMotor();
}


// ======================================================
// HAM SO SANH THOI GIAN micros() CO XU LY TRAN SO
// ======================================================
bool timeReachedUs(unsigned long nowUs, unsigned long targetUs) {
  return (long)(nowUs - targetUs) >= 0;
}


// ======================================================
// XU LY LENH SERIAL
// ======================================================
void handleSerialCommand() {
  if (!Serial.available()) {
    return;
  }

  char cmd = Serial.read();

  // Bo qua ky tu xuong dong cua Serial Monitor.
  if (cmd == '\n' || cmd == '\r') {
    return;
  }

  // ------------------------------
  // Lenh cho phan cua
  // ------------------------------
  if (cmd == 'x') {
    if (isDoorMoving()) {
      Serial.println("Cua dang di chuyen, chua nhan lenh x.");
    } else if (!doorOpened) {
      Serial.println("Nhan x -> bat dau mo cua 90 do.");
      startDoorOpening();
    } else {
      Serial.println("Cua da mo roi.");
    }
  }

  else if (cmd == 'g') {
    if (isDoorMoving()) {
      Serial.println("Cua dang di chuyen, chua nhan lenh g.");
    } else if (doorOpened) {
      Serial.println("Nhan g -> bat dau dong cua.");
      startDoorClosing();
    } else {
      Serial.println("Cua dang o vi tri ban dau.");
    }
  }

  // ------------------------------
  // Lenh cho phan ep
  // ------------------------------
  else if (cmd == 's') {
    startPressCycle();
  }

  else if (cmd == 'r') {
    resetPressSystem();
  }

  // ------------------------------
  // Lenh xem muc dung dich
  // ------------------------------
  else if (cmd == 'l') {
    printAllLiquidLevels();
  }

  // ------------------------------
  // Lenh doc muc day 2 thung rac (host Q509 poll moi 3s)
  // ------------------------------
  else if (cmd == 'u') {
    printAllTrashBinStatus();
  }

  // ------------------------------
  // Lenh cho motor cat DC 775
  // ------------------------------
  else if (cmd == '1') {
    setCuttingTime(1);
  }

  else if (cmd == '3') {
    setCuttingTime(3);
  }

  else if (cmd == '5') {
    setCuttingTime(5);
  }

  else if (cmd == 'y') {
    startCutterMotor();
  }

  else if (cmd == 'z') {
    stopCutterMotor("Nhan lenh dung z.");
  }

  // ------------------------------
  // Lenh test hai bom dung dich
  // ------------------------------
  else if (cmd == 't') {
    requestDetergentSpray("Lenh test t.");
  }

  else if (cmd == 'v') {
    requestMicrobeSpray("Lenh test v.");
  }

  else if (cmd == 'q') {
    stopAllPumps("Nhan lenh dung q.");
    detergentSprayRequested = false;
    microbeSprayRequested = false;
  }

  // ------------------------------
  // Lenh AUTO MODE (host Q509 gui)
  // A : bat auto -> sensor 5s lien tuc gui AUTO_TRIGGER, khong tu mo cua
  // M : tat auto -> sensor khong dieu khien gi nua, manual hoan toan
  // ------------------------------
  else if (cmd == 'A') {
    autoModeEnabled = true;
    detectStartTime = 0;
    // Neu sensor dang DETECT san (bui/tay/noise) -> doi CLEAR truoc khi dem
    if (digitalRead(SENSOR_PIN) == SENSOR_DETECTED) {
      waitForSensorClear = true;
    } else {
      waitForSensorClear = false;
    }
    Serial.println("AUTO_MODE_ON");
  }
  else if (cmd == 'M') {
    autoModeEnabled = false;
    detectStartTime = 0;
    waitForSensorClear = false;
    Serial.println("AUTO_MODE_OFF");
  }
}


// ======================================================
// PHAN 1: CAM BIEN QUANG + MOTOR CUA KHONG CHAN
// ======================================================
bool isDoorMoving() {
  return doorMotion != DOOR_STOPPED;
}

void handleDoorSystem() {
  bool sensorDetected = (digitalRead(SENSOR_PIN) == SENSOR_DETECTED);

  // Edge detection: bao host khi sensor chuyen detect <-> clear
  // (UI hien LED real-time, khong phai cho 5s)
  if (sensorDetected != lastSensorReported) {
    if (sensorDetected) {
      Serial.println("SENSOR_DETECT");
    } else {
      Serial.println("SENSOR_CLEAR");
    }
    lastSensorReported = sensorDetected;
  }

  if (isDoorMoving()) {
    return;
  }

  // Manual mode: sensor khong dieu khien cua (host quan ly).
  if (!autoModeEnabled) {
    detectStartTime = 0;
    return;
  }

  // Vua bat Auto ma sensor dang DETECT -> doi CLEAR truoc khi dem
  if (waitForSensorClear) {
    if (!sensorDetected) {
      waitForSensorClear = false;
    }
    detectStartTime = 0;
    sensorClearStartMs = 0;
    return;
  }

  // Auto mode: sensor 5s lien tuc -> gui AUTO_TRIGGER cho host
  if (sensorDetected) {
    // Hen reset trang thai clear vi sensor da detect tro lai
    sensorClearStartMs = 0;
    if (detectStartTime == 0) {
      detectStartTime = millis();
    }
    if (millis() - detectStartTime >= DETECT_TIME_MS) {
      if (millis() - lastAutoTriggerMs > AUTO_RETRIGGER_GUARD_MS) {
        Serial.println("AUTO_TRIGGER");
        lastAutoTriggerMs = millis();
      }
      detectStartTime = 0;
    }
  } else {
    // Debounce CLEAR: chi reset bo dem khi sensor da clear lien tuc >300ms.
    // Tranh jitter cua E18 reset bo dem 5s lien tuc.
    if (sensorClearStartMs == 0) {
      sensorClearStartMs = millis();
    } else if (millis() - sensorClearStartMs >= SENSOR_CLEAR_DEBOUNCE_MS) {
      detectStartTime = 0;
    }
  }
}

void startDoorOpening() {
  startDoorMove(DOOR_OPENING, DOOR_DIR_OPEN, DOOR_STEPS_90);
  detectStartTime = 0;
}

void startDoorClosing() {
  startDoorMove(DOOR_CLOSING, DOOR_DIR_CLOSE, DOOR_STEPS_90);
  detectStartTime = 0;
}

void startDoorMove(DoorMotionState motion, int direction, long steps) {
  if (isDoorMoving()) {
    return;
  }

  digitalWrite(DOOR_DIR_PIN, direction);
  digitalWrite(DOOR_STEP_PIN, LOW);

  doorPulseHigh = false;
  doorStepsRemaining = steps;
  doorMotion = motion;

  // Thay cho delay(5) cu: cho on dinh chieu quay nhung khong dung chuong trinh.
  doorNextEdgeUs = micros() + DIR_SETTLE_TIME_US;
}

void updateDoorMotor() {
  if (!isDoorMoving()) {
    return;
  }

  unsigned long nowUs = micros();

  if (!timeReachedUs(nowUs, doorNextEdgeUs)) {
    return;
  }

  if (!doorPulseHigh) {
    digitalWrite(DOOR_STEP_PIN, HIGH);
    doorPulseHigh = true;
    doorNextEdgeUs = nowUs + DOOR_STEP_HALF_PERIOD_US;
  } else {
    digitalWrite(DOOR_STEP_PIN, LOW);
    doorPulseHigh = false;
    doorStepsRemaining--;

    if (doorStepsRemaining <= 0) {
      finishDoorMove();
    } else {
      doorNextEdgeUs = nowUs + DOOR_STEP_HALF_PERIOD_US;
    }
  }
}

void finishDoorMove() {
  digitalWrite(DOOR_STEP_PIN, LOW);

  DoorMotionState completedMotion = doorMotion;
  doorMotion = DOOR_STOPPED;
  doorStepsRemaining = 0;
  doorPulseHigh = false;

  if (completedMotion == DOOR_OPENING) {
    doorOpened = true;
    doorOpenedTime = millis();
    Serial.println("Da mo cua 90 do.");
  } else if (completedMotion == DOOR_CLOSING) {
    doorOpened = false;
    doorOpenedTime = 0;
    detectStartTime = 0;
    Serial.println("Da dong cua ve vi tri ban dau.");
  }
}


// ======================================================
// PHAN 3: BAO MUC DUNG DICH XKC-Y25-PNP KHONG CHAN
// ======================================================
void initializeLiquidTank(LiquidTankStatus &tank) {
  tank.candidateHasLiquid = (digitalRead(tank.pin) == LIQUID_PRESENT);
  tank.stableHasLiquid = tank.candidateHasLiquid;
  tank.stableKnown = false;
  tank.candidateSince = millis();
}

void handleLiquidLevelSystem() {
  updateLiquidTank(detergentTank);
  updateLiquidTank(microbeTank);
}

void updateLiquidTank(LiquidTankStatus &tank) {
  bool nowHasLiquid = (digitalRead(tank.pin) == LIQUID_PRESENT);
  unsigned long nowMs = millis();

  // Tin hieu vua thay doi: bat dau dem lai thoi gian xac nhan.
  if (nowHasLiquid != tank.candidateHasLiquid) {
    tank.candidateHasLiquid = nowHasLiquid;
    tank.candidateSince = nowMs;
    return;
  }

  // Chua du 3 giay on dinh thi chua thong bao.
  if (nowMs - tank.candidateSince < LIQUID_CONFIRM_TIME_MS) {
    return;
  }

  // Chi in khi khoi dong da xac nhan lan dau hoac trang thai thuc su thay doi.
  if (!tank.stableKnown || tank.stableHasLiquid != tank.candidateHasLiquid) {
    tank.stableKnown = true;
    tank.stableHasLiquid = tank.candidateHasLiquid;
    printLiquidTankStatus(tank);
  }
}

void printLiquidTankStatus(const LiquidTankStatus &tank) {
  if (!tank.stableKnown) {
    Serial.print("LEVEL_");
    Serial.print(tank.code);
    Serial.println("_CHECKING");
    return;
  }

  if (tank.stableHasLiquid) {
    Serial.print("LEVEL_");
    Serial.print(tank.code);
    Serial.println("_OK");

    Serial.print(tank.name);
    Serial.println(": DU MUC (>=20%).");
  } else {
    Serial.print("LEVEL_");
    Serial.print(tank.code);
    Serial.println("_LOW");

    Serial.print("CANH BAO: ");
    Serial.print(tank.name);
    Serial.println(" DUOI 20% - CAN THEM DUNG DICH.");
  }
}

void printAllLiquidLevels() {
  Serial.println("----- TRANG THAI MUC DUNG DICH -----");
  printLiquidTankStatus(detergentTank);
  printLiquidTankStatus(microbeTank);
  Serial.println("------------------------------------");
}


// ======================================================
// PHAN 4: MOTOR CAT DC 775 + BTS7960 KHONG CHAN
// ======================================================
void setCuttingTime(byte minutes) {
  selectedCutMinutes = minutes;
  selectedCutDurationMs = (unsigned long)minutes * 10000UL;

  Serial.print("Da chon thoi gian cat: ");
  Serial.print(selectedCutMinutes);
  Serial.println(" phut.");

  if (cutterRunning) {
    Serial.println("Luu y: thoi gian moi se ap dung cho lan cat tiep theo.");
  }
}

void startCutterMotor() {
  if (cutterRunning) {
    Serial.println("Motor cat dang chay, khong khoi dong lai.");
    return;
  }

  // Can de ca hai chan enable HIGH de BTS7960 cho phep hoat dong.
  digitalWrite(CUTTER_REN_PIN, HIGH);
  digitalWrite(CUTTER_LEN_PIN, HIGH);

  // Chay mot chieu: RPWM duoc cap PWM, LPWM luon bang 0.
  analogWrite(CUTTER_LPWM_PIN, 0);
  analogWrite(CUTTER_RPWM_PIN, 0);

  cutterCurrentPwm = 0;
  cutterStartMs = millis();
  cutterLastRampMs = millis();
  cutterLastReportMs = millis();
  cutterRunning = true;

  Serial.print("BAT DAU CAT - thoi gian da set: ");
  Serial.print(selectedCutMinutes);
  Serial.println(" phut.");
}

void handleCutterSystem() {
  if (!cutterRunning) {
    return;
  }

  unsigned long nowMs = millis();

  // Het dung thoi gian nguoi dung da chon thi dung cap dien cho motor.
  if (nowMs - cutterStartMs >= selectedCutDurationMs) {
    stopCutterMotor("Het thoi gian da setting.");
    return;
  }

  // Ramp PWM khong chan: motor cat tang toc mem,
  // cac he thong loadcell/cua/muc dung dich van duoc xu ly.
  if (cutterCurrentPwm < CUTTER_PWM_TARGET &&
      nowMs - cutterLastRampMs >= CUTTER_RAMP_INTERVAL_MS) {
    cutterLastRampMs = nowMs;

    int nextPwm = cutterCurrentPwm + CUTTER_RAMP_STEP;
    if (nextPwm > CUTTER_PWM_TARGET) {
      nextPwm = CUTTER_PWM_TARGET;
    }

    cutterCurrentPwm = (byte)nextPwm;
    analogWrite(CUTTER_RPWM_PIN, cutterCurrentPwm);
  }

  // Chi bao trang thai moi 10 giay de tranh Serial lam cham he thong.
  if (nowMs - cutterLastReportMs >= 10000UL) {
    cutterLastReportMs = nowMs;

    unsigned long elapsedSec = (nowMs - cutterStartMs) / 1000UL;
    unsigned long totalSec = selectedCutDurationMs / 1000UL;
    unsigned long remainingSec =
      (totalSec > elapsedSec) ? (totalSec - elapsedSec) : 0;

    Serial.print("Motor cat dang chay | PWM: ");
    Serial.print(cutterCurrentPwm);
    Serial.print(" | Con lai: ");
    Serial.print(remainingSec);
    Serial.println(" giay.");
  }
}

void stopCutterMotor(const char* reason) {
  // Tat PWM, sau do disable driver.
  // Day la dung cap dien; luoi dao thuc te co the con quay theo quan tinh.
  analogWrite(CUTTER_RPWM_PIN, 0);
  analogWrite(CUTTER_LPWM_PIN, 0);
  digitalWrite(CUTTER_REN_PIN, LOW);
  digitalWrite(CUTTER_LEN_PIN, LOW);

  bool wasRunning = cutterRunning;
  cutterRunning = false;
  cutterCurrentPwm = 0;

  if (wasRunning) {
    Serial.print("DUNG MOTOR CAT - ");
    Serial.println(reason);
  }
}


// ======================================================
// PHAN 5: HAI BOM DUNG DICH KHONG CHAN
// ======================================================
void requestDetergentSpray(const char* reason) {
  if (detergentSprayRequested || pumpState == DETERGENT_PUMP_RUNNING) {
    return;
  }

  detergentSprayRequested = true;
  Serial.print("YEU CAU BOM CHAT TAY RUA - ");
  Serial.println(reason);
}

void requestMicrobeSpray(const char* reason) {
  if (microbeSprayRequested || pumpState == MICROBE_PUMP_RUNNING) {
    return;
  }

  microbeSprayRequested = true;
  Serial.print("YEU CAU BOM VI SINH - ");
  Serial.println(reason);
}

bool canUseTankForSpray(const LiquidTankStatus &tank) {
  if (!tank.stableKnown) {
    Serial.print("KHONG BOM: Chua xac nhan muc dung dich cua ");
    Serial.println(tank.name);
    return false;
  }

  if (!tank.stableHasLiquid) {
    Serial.print("KHONG BOM: ");
    Serial.print(tank.name);
    Serial.println(" dang duoi 20%, can them dung dich.");
    return false;
  }

  return true;
}

void startDetergentSpray() {
  digitalWrite(MICROBE_PUMP_RELAY_PIN, RELAY_OFF);
  digitalWrite(DETERGENT_PUMP_RELAY_PIN, RELAY_ON);
  pumpState = DETERGENT_PUMP_RUNNING;
  pumpStartMs = millis();
  Serial.println("BAT DAU BOM CHAT TAY RUA (khoang vo co).");
}

void startMicrobeSpray() {
  digitalWrite(DETERGENT_PUMP_RELAY_PIN, RELAY_OFF);
  digitalWrite(MICROBE_PUMP_RELAY_PIN, RELAY_ON);
  pumpState = MICROBE_PUMP_RUNNING;
  pumpStartMs = millis();
  Serial.println("BAT DAU BOM VI SINH (khoang huu co).");
}

void stopAllPumps(const char* reason) {
  digitalWrite(DETERGENT_PUMP_RELAY_PIN, RELAY_OFF);
  digitalWrite(MICROBE_PUMP_RELAY_PIN, RELAY_OFF);

  if (pumpState != PUMPS_IDLE) {
    Serial.print("DUNG BOM - ");
    Serial.println(reason);
  }

  pumpState = PUMPS_IDLE;
}

void handlePumpSystem() {
  unsigned long nowMs = millis();

  // Bom chi chay khi host gui lenh 't' (chat tay rua) hoac 'v' (vi sinh).
  // Mega tu dung bom sau 3s, hoac khi nhan lenh 'q' / mat dung dich.

  // Dang bom: het thoi gian hoac het dung dich thi dung ngay.
  if (pumpState == DETERGENT_PUMP_RUNNING) {
    if (detergentTank.stableKnown && !detergentTank.stableHasLiquid) {
      stopAllPumps("Binh chat tay rua duoi 20%.");
      return;
    }

    if (nowMs - pumpStartMs >= DETERGENT_SPRAY_TIME_MS) {
      stopAllPumps("Het thoi gian phun chat tay rua.");
    }
    return;
  }

  if (pumpState == MICROBE_PUMP_RUNNING) {
    if (microbeTank.stableKnown && !microbeTank.stableHasLiquid) {
      stopAllPumps("Binh vi sinh duoi 20%.");
      return;
    }

    if (nowMs - pumpStartMs >= MICROBE_SPRAY_TIME_MS) {
      stopAllPumps("Het thoi gian phun vi sinh.");
    }
    return;
  }

  // Khong chay dong thoi hai bom; uu tien yeu cau phun tu khoang vo co.
  if (detergentSprayRequested) {
    detergentSprayRequested = false;
    if (canUseTankForSpray(detergentTank)) {
      startDetergentSpray();
    }
    return;
  }

  if (microbeSprayRequested) {
    microbeSprayRequested = false;
    if (canUseTankForSpray(microbeTank)) {
      startMicrobeSpray();
    }
  }
}


// ======================================================
// PHAN 2: LOADCELL + MOTOR EP KHONG CHAN
// ======================================================
void startPressCycle() {
  stopPressMotor();

  Serial.println("Bat dau chu ky ep.");
  Serial.println("Khong tac dong luc len loadcell. Se tare sau 2 giay...");

  over_count = 0;
  currentForce = 0;
  tareRawSum = 0;
  tareSampleCount = 0;

  pressState = PRESS_TARE_WAIT;
  pressStateStartTime = millis();
  lastPrintTime = millis();
}

void resetPressSystem() {
  stopPressMotor();

  over_count = 0;
  currentForce = 0;
  tareRawSum = 0;
  tareSampleCount = 0;

  pressState = PRESS_IDLE;

  Serial.println("Da dung/reset phan ep ve IDLE.");
}

void handlePressSystem() {
  // ------------------------------
  // Doi nguoi dung bo tay/tai ra khoi loadcell
  // ------------------------------
  if (pressState == PRESS_TARE_WAIT) {
    if (millis() - pressStateStartTime >= TARE_WAIT_MS) {
      tareRawSum = 0;
      tareSampleCount = 0;
      pressState = PRESS_TARE_COLLECT;
      pressStateStartTime = millis();

      Serial.println("Dang lay 20 mau tare khong chan...");
    }
    return;
  }

  // ------------------------------
  // Lay mau tare tung lan khi HX711 san sang
  // Door motor van co the chay trong giai doan nay.
  // ------------------------------
  if (pressState == PRESS_TARE_COLLECT) {
    if (scale.is_ready()) {
      tareRawSum += scale.read();
      tareSampleCount++;

      if (tareSampleCount >= TARE_SAMPLE_TARGET) {
        long newOffset = tareRawSum / (long)TARE_SAMPLE_TARGET;
        scale.set_offset(newOffset);

        Serial.println("Da tare xong. Motor ep bat dau quay chieu ep.");

        over_count = 0;
        currentForce = 0;
        lastPrintTime = millis();

        startPressMotor(PRESS_DIR_DOWN);
        pressState = PRESSING;
        return;
      }
    }

    if (millis() - pressStateStartTime > TARE_COLLECT_TIMEOUT_MS) {
      stopPressMotor();
      pressState = PRESS_IDLE;
      Serial.println("LOI: Khong lay du 20 mau HX711. HUY CHU KY EP, kiem tra day cam bien.");
    }
    return;
  }

  // ------------------------------
  // Ep xuong va doc luc
  // ------------------------------
  if (pressState == PRESSING) {
    // Chi doc khi HX711 co mau moi; nhu vay khong cho ham doc cho doi du lieu.
    if (scale.is_ready()) {
      currentForce = readForceReady();

      if (currentForce >= force_limit_g) {
        over_count++;
      } else {
        over_count = 0;
      }

      if (over_count >= required_count) {
        stopPressMotor();

        Serial.print("Luc nen: ");
        Serial.print(currentForce, 1);
        Serial.println(" g | DAT NGUONG -> DUNG EP.");

        Serial.println("Cho 300ms truoc khi quay nguoc...");
        pressState = PRESS_REVERSE_WAIT;
        pressStateStartTime = millis();
        return;
      }
    }

    if (millis() - lastPrintTime >= 300UL) {
      lastPrintTime = millis();

      Serial.print("Luc nen: ");
      Serial.print(currentForce, 1);
      Serial.print(" g | Over count: ");
      Serial.print(over_count);
      Serial.println(" | Dang ep...");
    }
    return;
  }

  // ------------------------------
  // Dung nhe truoc khi dao chieu, khong dung delay()
  // ------------------------------
  if (pressState == PRESS_REVERSE_WAIT) {
    if (millis() - pressStateStartTime >= 300UL) {
      Serial.println("Bat dau quay nguoc ve cong tac hanh trinh...");
      startPressMotor(PRESS_DIR_UP);
      pressState = REVERSING;
      lastPrintTime = millis();
    }
    return;
  }

  // ------------------------------
  // Quay nguoc ve cong tac hanh trinh
  // ------------------------------
  if (pressState == REVERSING) {
    if (isLimitPressed()) {
      stopPressMotor();
      pressState = PRESS_DONE;
      lastPrintTime = millis();
      Serial.println("DA CHAM CONG TAC HANH TRINH -> DUNG MOTOR EP.");
      requestDetergentSpray("Ban ep khoang vo co da cham cong tac hanh trinh.");
      return;
    }

    if (millis() - lastPrintTime >= 300UL) {
      lastPrintTime = millis();
      Serial.println("Motor ep dang quay nguoc len... chua cham cong tac.");
    }
    return;
  }

  // ------------------------------
  // Chu ky ep da hoan thanh — KHONG spam log nua,
  // chi cho lenh 'r' (reset) hoac 's' (chay lai) tu host.
  // ------------------------------
}

void startPressMotor(int direction) {
  digitalWrite(PRESS_DIR_PIN, direction);
  digitalWrite(PRESS_PUL_PIN, LOW);

  pressPulseHigh = false;
  pressMotorRunning = true;

  // Doi on dinh chieu quay ma khong lam dung he thong.
  pressNextEdgeUs = micros() + DIR_SETTLE_TIME_US;
}

void stopPressMotor() {
  pressMotorRunning = false;
  pressPulseHigh = false;
  digitalWrite(PRESS_PUL_PIN, LOW);
}

void updatePressMotor() {
  if (!pressMotorRunning) {
    return;
  }

  unsigned long nowUs = micros();

  if (!timeReachedUs(nowUs, pressNextEdgeUs)) {
    return;
  }

  if (!pressPulseHigh) {
    digitalWrite(PRESS_PUL_PIN, HIGH);
    pressPulseHigh = true;
    pressNextEdgeUs = nowUs + PRESS_STEP_HALF_PERIOD_US;
  } else {
    digitalWrite(PRESS_PUL_PIN, LOW);
    pressPulseHigh = false;
    pressNextEdgeUs = nowUs + PRESS_STEP_HALF_PERIOD_US;
  }
}

bool isLimitPressed() {
  // Dung INPUT_PULLUP:
  // Chua nhan = HIGH, nhan cong tac = LOW.
  return digitalRead(LIMIT_PIN) == LOW;
}

float readForceReady() {
  // Ham nay chi duoc goi sau khi scale.is_ready() == true,
  // de tranh cho du lieu va lam cham hai motor.
  float force_g = scale.get_units(1);

  if (force_g < 0) {
    force_g = -force_g;
  }

  if (force_g < dead_zone_g) {
    force_g = 0;
  }

  return force_g;
}


// ======================================================
// PHAN 6: DOC 2 JSN-SR04T BAO DAY THUNG RAC
// (cach doc da test thuc te voi JSN-SR04T V3.3)
// ======================================================
void initializeTrashBinSystem() {
  isolateAllJSNPins();

  inorganicTrashBin.full = false;
  inorganicTrashBin.known = false;
  inorganicTrashBin.lastDistanceCm = -1.0;
  inorganicTrashBin.fullStartMs = 0;
  inorganicTrashBin.clearStartMs = 0;

  organicTrashBin.full = false;
  organicTrashBin.known = false;
  organicTrashBin.lastDistanceCm = -1.0;
  organicTrashBin.fullStartMs = 0;
  organicTrashBin.clearStartMs = 0;

  lastTrashBinReadMs = millis();
  nextReadInorganicBin = true;
}

void isolateAllJSNPins() {
  // Cach ly chan giup 2 module JSN-SR04T V3.3 doc on dinh khi cam dong thoi:
  // dua TAT CA chan ve INPUT truoc khi kich tung cam bien.
  pinMode(INORG_BIN_TRIG_PIN, INPUT);
  pinMode(INORG_BIN_ECHO_PIN, INPUT);
  pinMode(ORG_BIN_TRIG_PIN, INPUT);
  pinMode(ORG_BIN_ECHO_PIN, INPUT);
  delay(5);
}

bool canReadTrashBinsNow() {
  // pulseIn co the chan ngan -> chi doc khi cac co cau dang ranh
  // de khong lam giat xung step cua motor.
  if (isDoorMoving())          return false;
  if (pressMotorRunning)       return false;
  if (cutterRunning)           return false;
  if (pumpState != PUMPS_IDLE) return false;
  return true;
}

void handleTrashBinSystem() {
  unsigned long nowMs = millis();

  if (nowMs - lastTrashBinReadMs < TRASH_BIN_READ_INTERVAL_MS) {
    return;
  }
  if (!canReadTrashBinsNow()) {
    return;
  }

  lastTrashBinReadMs = nowMs;

  // Doc LUAN PHIEN tung cam bien (moi cam bien ~2 giay/lan).
  if (nextReadInorganicBin) {
    float d = readTrashDistanceOnce(inorganicTrashBin, organicTrashBin);
    updateTrashBinStatus(inorganicTrashBin, d);
  } else {
    float d = readTrashDistanceOnce(organicTrashBin, inorganicTrashBin);
    updateTrashBinStatus(organicTrashBin, d);
  }
  nextReadInorganicBin = !nextReadInorganicBin;
}

float readTrashDistanceOnce(TrashBinStatus &bin, TrashBinStatus &otherBin) {
  isolateAllJSNPins();

  // Cam bien khong do -> de INPUT de khong anh huong.
  pinMode(otherBin.trigPin, INPUT);
  pinMode(otherBin.echoPin, INPUT);

  pinMode(bin.trigPin, OUTPUT);
  pinMode(bin.echoPin, INPUT);

  digitalWrite(bin.trigPin, LOW);
  delayMicroseconds(20);

  // JSN-SR04T cua he nay test on dinh voi xung TRIG 50us.
  digitalWrite(bin.trigPin, HIGH);
  delayMicroseconds(50);
  digitalWrite(bin.trigPin, LOW);

  unsigned long duration = pulseIn(bin.echoPin, HIGH, 60000UL);
  if (duration == 0) {
    return -1.0;                 // khong nhan duoc echo
  }

  float distanceCm = duration / 58.0;

  // Loai mau bat thuong: JSN-SR04T doc duoi ~20 cm khong dang tin.
  if (distanceCm < 20.0 || distanceCm > 450.0) {
    return -1.0;
  }
  return distanceCm;
}

void updateTrashBinStatus(TrashBinStatus &bin, float distanceCm) {
  if (distanceCm < 0) {
    // Mot lan mat echo -> khong doi trang thai.
    return;
  }

  unsigned long nowMs = millis();
  bin.known = true;
  bin.lastDistanceCm = distanceCm;

  if (!bin.full) {
    // Dang OK -> co len FULL khi <= fullDistanceCm lien tuc du lau.
    if (distanceCm <= bin.fullDistanceCm) {
      if (bin.fullStartMs == 0) bin.fullStartMs = nowMs;
      if (nowMs - bin.fullStartMs >= TRASH_BIN_CONFIRM_TIME_MS) {
        bin.full = true;
        bin.clearStartMs = 0;
        Serial.print("BIN_"); Serial.print(bin.code); Serial.println("_FULL");
        Serial.print("CANH BAO: "); Serial.print(bin.name);
        Serial.print(" DAY - CAN DO RAC. Khoang cach = ");
        Serial.print(distanceCm, 1); Serial.println(" cm");
      }
    } else {
      bin.fullStartMs = 0;
    }
  } else {
    // Dang FULL -> ve OK khi >= clearDistanceCm lien tuc du lau.
    if (distanceCm >= bin.clearDistanceCm) {
      if (bin.clearStartMs == 0) bin.clearStartMs = nowMs;
      if (nowMs - bin.clearStartMs >= TRASH_BIN_CONFIRM_TIME_MS) {
        bin.full = false;
        bin.fullStartMs = 0;
        Serial.print("BIN_"); Serial.print(bin.code); Serial.println("_OK");
        Serial.print(bin.name);
        Serial.print(": DA DO RAC / CHUA DAY. Khoang cach = ");
        Serial.print(distanceCm, 1); Serial.println(" cm");
      }
    } else {
      bin.clearStartMs = 0;
    }
  }
}

void printTrashBinStatus(const TrashBinStatus &bin) {
  // Dong cho host Q509 parse: BIN_<code>_FULL / _OK / _CHECKING
  // kem khoang cach de tien debug tren Serial Monitor / Nhat ky UI.
  Serial.print("BIN_");
  Serial.print(bin.code);

  if (!bin.known) {
    Serial.println("_CHECKING");
    Serial.print(bin.name);
    Serial.println(": CHUA CO DU LIEU KHOANG CACH (chua doc duoc echo).");
    return;
  }

  if (bin.full) {
    Serial.println("_FULL");
    Serial.print(bin.name);
    Serial.print(": DAY - CAN DO RAC | distance = ");
  } else {
    Serial.println("_OK");
    Serial.print(bin.name);
    Serial.print(": CHUA DAY | distance = ");
  }
  Serial.print(bin.lastDistanceCm, 1);
  Serial.println(" cm");
}

// Host Q509 gui 'u' -> in ngay trang thai 2 thung (UI cap nhat moi 3s).
void printAllTrashBinStatus() {
  Serial.println("----- TRANG THAI THUNG RAC -----");
  printTrashBinStatus(inorganicTrashBin);
  printTrashBinStatus(organicTrashBin);
  Serial.println("--------------------------------");
}
