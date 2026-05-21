#include "esp_camera.h"
#include "FS.h"
#include "SD_MMC.h"
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <time.h>

#define WIFI_SSID  "Zer0"
#define WIFI_PASS  "XXXXXXX"

#define BROKER_HOST  "XXXXXXXXXXX"
#define BROKER_PORT  8883
#define CLIENT_ID    "esp32cam-tamper-subscriber"
#define SECTOR_ID    "S1"
#define EVENT_TOPIC  "droplock/" SECTOR_ID "/+/events"

// Store these files at the SD card root using the same names as the locker module.
#define TLS_CA_CERT_PATH      "/ca.crt"
#define TLS_CLIENT_CERT_PATH  "/client.crt"
#define TLS_CLIENT_KEY_PATH   "/client.key"

// Fill these in before flashing if the ESP32-CAM should email photos directly.
#define SMTP_HOST        "smtp.gmail.com"
#define SMTP_PORT        587
#define SMTP_USERNAME    "XXXXXXXXX"
#define SMTP_PASSWORD    "XXXXXXXXXXX"
#define SMTP_FROM_EMAIL  "XXXXXXXXXX"
#define SMTP_FROM_NAME   "DropLock ESP32-CAM"
#define SMTP_TO_EMAIL    "XXXXXXXXXX"
#define SMTP_TO_NAME     "XXXXXXX"

#define MAX_LOCKERS           4
#define MAX_PENDING_CAPTURES  4

#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

struct LockerTamperState {
  String lockerId;
  bool known = false;
  bool tamper = false;
};

struct PendingCapture {
  bool active = false;
  String lockerId;
  String eventType;
};

WiFiClientSecure mqttTlsClient;
WiFiClientSecure smtpClient;
PubSubClient mqttClient(mqttTlsClient);

String mqttCaCert;
String mqttClientCert;
String mqttClientKey;
LockerTamperState lockerStates[MAX_LOCKERS];
PendingCapture pendingCaptures[MAX_PENDING_CAPTURES];

bool initCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAMESIZE_SVGA;
  config.jpeg_quality = 12;
  config.fb_count     = 1;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[CAM] init failed: 0x%x\n", err);
    return false;
  }

  camera_fb_t *fb = esp_camera_fb_get();
  if (fb) {
    esp_camera_fb_return(fb);
  }

  Serial.println("[CAM] ready");
  return true;
}

bool initSD() {
  if (!SD_MMC.begin("/sdcard", true)) {
    Serial.println("[SD] mount failed");
    return false;
  }

  if (SD_MMC.cardType() == CARD_NONE) {
    Serial.println("[SD] no card");
    return false;
  }

  Serial.printf("[SD] ready (%.1f GB)\n",
                (float)SD_MMC.cardSize() / (1024.0 * 1024 * 1024));
  return true;
}

String readSdTextFile(const char *path) {
  File f = SD_MMC.open(path, FILE_READ);
  if (!f) {
    Serial.printf("[SD] cannot open %s\n", path);
    return "";
  }

  String content;
  while (f.available()) {
    content += (char)f.read();
  }
  f.close();

  if (!content.endsWith("\n")) {
    content += "\n";
  }

  return content;
}

bool loadMqttTlsCertificates() {
  mqttCaCert = readSdTextFile(TLS_CA_CERT_PATH);
  mqttClientCert = readSdTextFile(TLS_CLIENT_CERT_PATH);
  mqttClientKey = readSdTextFile(TLS_CLIENT_KEY_PATH);

  if (mqttCaCert.length() == 0 || mqttClientCert.length() == 0 || mqttClientKey.length() == 0) {
    Serial.println("[TLS] missing MQTT certificate files on SD");
    return false;
  }

  mqttTlsClient.setCACert(mqttCaCert.c_str());
  mqttTlsClient.setCertificate(mqttClientCert.c_str());
  mqttTlsClient.setPrivateKey(mqttClientKey.c_str());
  mqttTlsClient.setTimeout(15000);

  Serial.println("[TLS] MQTT certificates loaded from SD");
  return true;
}

void connectWiFi() {
  Serial.printf("[WiFi] connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  while (WiFi.status() != WL_CONNECTED) {
    Serial.print('.');
    delay(500);
  }

  Serial.printf(" ok IP=%s\n", WiFi.localIP().toString().c_str());
}

bool syncTime() {
  configTime(3600, 0, "pool.ntp.org", "time.nist.gov");
  Serial.print("[NTP] syncing");

  struct tm t;
  for (int attempt = 0; attempt < 60; attempt++) {
    if (getLocalTime(&t)) {
      Serial.println(" ok");
      return true;
    }
    Serial.print('.');
    delay(500);
  }

  Serial.println(" failed");
  return false;
}

String timestamp() {
  struct tm t;
  if (!getLocalTime(&t)) {
    return String(millis());
  }

  char buf[20];
  strftime(buf, sizeof(buf), "%Y%m%d_%H%M%S", &t);
  return String(buf);
}

bool isSafeFilenameChar(char c) {
  return (c >= 'a' && c <= 'z') ||
         (c >= 'A' && c <= 'Z') ||
         (c >= '0' && c <= '9') ||
         c == '_' || c == '-';
}

String sanitizePathPart(String value) {
  value.trim();
  value.replace(" ", "_");

  for (int i = 0; i < value.length(); i++) {
    if (!isSafeFilenameChar(value.charAt(i))) {
      value.setCharAt(i, '_');
    }
  }

  if (value.length() == 0) {
    return "unknown_locker";
  }
  return value;
}

String baseName(const String &path) {
  int slash = path.lastIndexOf('/');
  if (slash < 0) {
    return path;
  }
  return path.substring(slash + 1);
}

String lockerIdFromTopic(const char *topic) {
  String topicText(topic);
  String prefix = String("droplock/") + String(SECTOR_ID) + "/";
  String suffix = "/events";

  if (!topicText.startsWith(prefix) || !topicText.endsWith(suffix)) {
    return "";
  }

  return topicText.substring(prefix.length(), topicText.length() - suffix.length());
}

bool parseTamperValue(JsonVariantConst value, bool &tamper) {
  if (value.isNull()) {
    return false;
  }

  if (value.is<bool>()) {
    tamper = value.as<bool>();
    return true;
  }

  if (value.is<int>()) {
    tamper = value.as<int>() != 0;
    return true;
  }

  if (value.is<const char *>()) {
    String text = value.as<const char *>();
    text.trim();
    text.toLowerCase();

    if (text == "true" || text == "1" || text == "yes") {
      tamper = true;
      return true;
    }
    if (text == "false" || text == "0" || text == "no") {
      tamper = false;
      return true;
    }
  }

  return false;
}

int lockerStateIndex(const String &lockerId) {
  int emptyIndex = -1;

  for (int i = 0; i < MAX_LOCKERS; i++) {
    if (lockerStates[i].known && lockerStates[i].lockerId == lockerId) {
      return i;
    }
    if (!lockerStates[i].known && emptyIndex < 0) {
      emptyIndex = i;
    }
  }

  if (emptyIndex >= 0) {
    lockerStates[emptyIndex].known = true;
    lockerStates[emptyIndex].lockerId = lockerId;
    lockerStates[emptyIndex].tamper = false;
  }

  return emptyIndex;
}

bool queueCapture(const String &lockerId, const String &eventType) {
  for (int i = 0; i < MAX_PENDING_CAPTURES; i++) {
    if (pendingCaptures[i].active && pendingCaptures[i].lockerId == lockerId) {
      Serial.printf("[CAM] capture already queued for %s\n", lockerId.c_str());
      return true;
    }
  }

  for (int i = 0; i < MAX_PENDING_CAPTURES; i++) {
    if (!pendingCaptures[i].active) {
      pendingCaptures[i].active = true;
      pendingCaptures[i].lockerId = lockerId;
      pendingCaptures[i].eventType = eventType;
      Serial.printf("[CAM] queued tamper capture for %s\n", lockerId.c_str());
      return true;
    }
  }

  Serial.printf("[CAM] capture queue full; dropping alert for %s\n", lockerId.c_str());
  return false;
}

String base64EncodeBytes(const uint8_t *data, size_t len) {
  static const char table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  String out;
  out.reserve(((len + 2) / 3) * 4);

  for (size_t i = 0; i < len; i += 3) {
    uint32_t value = ((uint32_t)data[i]) << 16;
    bool hasSecond = i + 1 < len;
    bool hasThird = i + 2 < len;

    if (hasSecond) {
      value |= ((uint32_t)data[i + 1]) << 8;
    }
    if (hasThird) {
      value |= data[i + 2];
    }

    out += table[(value >> 18) & 0x3F];
    out += table[(value >> 12) & 0x3F];
    out += hasSecond ? table[(value >> 6) & 0x3F] : '=';
    out += hasThird ? table[value & 0x3F] : '=';
  }

  return out;
}

String base64EncodeText(const String &text) {
  return base64EncodeBytes((const uint8_t *)text.c_str(), text.length());
}

bool smtpWaitFor(int expectedCode, uint32_t timeoutMs = 15000) {
  uint32_t started = millis();

  while (millis() - started < timeoutMs) {
    while (smtpClient.available()) {
      String line = smtpClient.readStringUntil('\n');
      line.trim();

      if (line.length() > 0) {
        Serial.printf("[SMTP] << %s\n", line.c_str());
      }

      if (line.length() >= 4 && line.charAt(3) == ' ') {
        int code = line.substring(0, 3).toInt();
        return code == expectedCode;
      }
    }
    delay(10);
  }

  Serial.printf("[SMTP] timeout waiting for %d\n", expectedCode);
  return false;
}

bool smtpCommand(const String &command, int expectedCode, bool sensitive = false) {
  Serial.printf("[SMTP] >> %s\n", sensitive ? "<hidden>" : command.c_str());
  smtpClient.print(command);
  smtpClient.print("\r\n");
  return smtpWaitFor(expectedCode);
}

bool smtpWriteFileBase64(const String &path) {
  File f = SD_MMC.open(path.c_str(), FILE_READ);
  if (!f) {
    Serial.printf("[SMTP] cannot open attachment %s\n", path.c_str());
    return false;
  }

  uint8_t buffer[57];
  while (true) {
    int n = f.read(buffer, sizeof(buffer));
    if (n <= 0) {
      break;
    }

    smtpClient.print(base64EncodeBytes(buffer, (size_t)n));
    smtpClient.print("\r\n");
    mqttClient.loop();
    delay(1);
  }

  f.close();
  return true;
}

bool sendPhotoEmail(const String &path, const String &lockerId, const String &eventType) {
  if (String(SMTP_USERNAME).length() == 0 ||
      String(SMTP_PASSWORD).length() == 0 ||
      String(SMTP_FROM_EMAIL).length() == 0 ||
      String(SMTP_TO_EMAIL).length() == 0) {
    Serial.println("[SMTP] email settings are blank; photo will stay on SD");
    return false;
  }

  if (mqttClient.connected()) {
    Serial.println("[MQTT] disconnecting while SMTP sends photo");
    mqttClient.disconnect();
    delay(250);
  }

  smtpClient.stop();
  smtpClient.setInsecure();
  smtpClient.setTimeout(20000);

  Serial.printf("[SMTP] connecting to %s:%d\n", SMTP_HOST, SMTP_PORT);
  if (!smtpClient.connect(SMTP_HOST, SMTP_PORT)) {
    Serial.println("[SMTP] connection failed");
    return false;
  }

  if (!smtpWaitFor(220)) { smtpClient.stop(); return false; }
  if (!smtpCommand("EHLO esp32cam-droplock", 250)) { smtpClient.stop(); return false; }
  if (!smtpCommand("AUTH LOGIN", 334)) { smtpClient.stop(); return false; }
  if (!smtpCommand(base64EncodeText(SMTP_USERNAME), 334, true)) { smtpClient.stop(); return false; }
  if (!smtpCommand(base64EncodeText(SMTP_PASSWORD), 235, true)) { smtpClient.stop(); return false; }
  if (!smtpCommand(String("MAIL FROM:<") + String(SMTP_FROM_EMAIL) + ">", 250)) { smtpClient.stop(); return false; }
  if (!smtpCommand(String("RCPT TO:<") + String(SMTP_TO_EMAIL) + ">", 250)) { smtpClient.stop(); return false; }
  if (!smtpCommand("DATA", 354)) { smtpClient.stop(); return false; }

  String filename = baseName(path);
  String boundary = String("DropLockBoundary") + String(millis());
  String subject = String("DropLock tamper photo - ") + lockerId;

  smtpClient.print("From: ");
  smtpClient.print(SMTP_FROM_NAME);
  smtpClient.print(" <");
  smtpClient.print(SMTP_FROM_EMAIL);
  smtpClient.print(">\r\n");
  smtpClient.print("To: ");
  smtpClient.print(SMTP_TO_NAME);
  smtpClient.print(" <");
  smtpClient.print(SMTP_TO_EMAIL);
  smtpClient.print(">\r\n");
  smtpClient.print("Subject: ");
  smtpClient.print(subject);
  smtpClient.print("\r\n");
  smtpClient.print("MIME-Version: 1.0\r\n");
  smtpClient.print("Content-Type: multipart/mixed; boundary=\"");
  smtpClient.print(boundary);
  smtpClient.print("\"\r\n\r\n");

  smtpClient.print("--");
  smtpClient.print(boundary);
  smtpClient.print("\r\n");
  smtpClient.print("Content-Type: text/plain; charset=\"UTF-8\"\r\n");
  smtpClient.print("Content-Transfer-Encoding: 7bit\r\n\r\n");
  smtpClient.print("DropLock tamper was detected.\r\n\r\n");
  smtpClient.print("Sector: ");
  smtpClient.print(SECTOR_ID);
  smtpClient.print("\r\nLocker: ");
  smtpClient.print(lockerId);
  smtpClient.print("\r\nEvent: ");
  if (eventType.length()) {
    smtpClient.print(eventType);
  } else {
    smtpClient.print("UNKNOWN");
  }
  smtpClient.print("\r\nCaptured: ");
  smtpClient.print(timestamp());
  smtpClient.print("\r\n\r\n");

  smtpClient.print("--");
  smtpClient.print(boundary);
  smtpClient.print("\r\n");
  smtpClient.print("Content-Type: image/jpeg; name=\"");
  smtpClient.print(filename);
  smtpClient.print("\"\r\n");
  smtpClient.print("Content-Disposition: attachment; filename=\"");
  smtpClient.print(filename);
  smtpClient.print("\"\r\n");
  smtpClient.print("Content-Transfer-Encoding: base64\r\n\r\n");

  if (!smtpWriteFileBase64(path)) {
    smtpClient.stop();
    return false;
  }

  smtpClient.print("\r\n--");
  smtpClient.print(boundary);
  smtpClient.print("--\r\n.\r\n");

  bool accepted = smtpWaitFor(250, 30000);
  smtpCommand("QUIT", 221);
  smtpClient.stop();

  if (accepted) {
    Serial.printf("[SMTP] sent %s\n", path.c_str());
  }

  return accepted;
}

bool capturePhotoToSD(const String &lockerId, String &pathOut) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[CAM] capture failed");
    return false;
  }

  String filename = String("/tamper_") + sanitizePathPart(lockerId) + "_" + timestamp() + ".jpg";
  File f = SD_MMC.open(filename.c_str(), FILE_WRITE);
  if (!f) {
    Serial.printf("[SD] cannot write %s\n", filename.c_str());
    esp_camera_fb_return(fb);
    return false;
  }

  size_t photoLen = fb->len;
  size_t written = f.write(fb->buf, photoLen);
  f.close();
  esp_camera_fb_return(fb);

  if (written != photoLen) {
    Serial.printf("[SD] short write %s (%u/%u bytes)\n",
                  filename.c_str(), (unsigned int)written, (unsigned int)photoLen);
    return false;
  }

  pathOut = filename;
  Serial.printf("[SD] saved %s (%u bytes)\n", pathOut.c_str(), (unsigned int)written);
  return true;
}

void captureSendDelete(const String &lockerId, const String &eventType) {
  String photoPath;
  if (!capturePhotoToSD(lockerId, photoPath)) {
    return;
  }

  if (!sendPhotoEmail(photoPath, lockerId, eventType)) {
    Serial.printf("[SD] keeping unsent photo %s\n", photoPath.c_str());
    return;
  }

  if (SD_MMC.remove(photoPath.c_str())) {
    Serial.printf("[SD] deleted sent photo %s\n", photoPath.c_str());
  } else {
    Serial.printf("[SD] failed to delete sent photo %s\n", photoPath.c_str());
  }
}

void processPendingCaptures() {
  for (int i = 0; i < MAX_PENDING_CAPTURES; i++) {
    if (!pendingCaptures[i].active) {
      continue;
    }

    String lockerId = pendingCaptures[i].lockerId;
    String eventType = pendingCaptures[i].eventType;
    pendingCaptures[i].active = false;
    pendingCaptures[i].lockerId = "";
    pendingCaptures[i].eventType = "";

    captureSendDelete(lockerId, eventType);
    return;
  }
}

void onMessage(char *topic, byte *payload, unsigned int length) {
  StaticJsonDocument<768> doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.printf("[MQTT] invalid JSON on %s\n", topic);
    return;
  }

  bool tamper = false;
  const char *eventTypeRaw = doc["type"] | "";
  String eventType(eventTypeRaw);

  if (!parseTamperValue(doc["tamper"], tamper)) {
    if (eventType.equalsIgnoreCase("TAMPER")) {
      tamper = true;
    } else {
      return;
    }
  }

  const char *payloadLockerId = doc["lockerId"] | "";
  String lockerId = String(payloadLockerId);
  lockerId.trim();

  if (lockerId.length() == 0) {
    lockerId = lockerIdFromTopic(topic);
  }
  if (lockerId.length() == 0) {
    Serial.printf("[MQTT] tamper event without locker id on %s\n", topic);
    return;
  }

  int stateIndex = lockerStateIndex(lockerId);
  bool alreadyTampered = stateIndex >= 0 && lockerStates[stateIndex].tamper;

  if (stateIndex >= 0) {
    lockerStates[stateIndex].tamper = tamper;
  } else {
    Serial.printf("[MQTT] max lockers reached; tracking skipped for %s\n", lockerId.c_str());
  }

  Serial.printf("[MQTT] %s tamper=%s type=%s\n",
                lockerId.c_str(),
                tamper ? "true" : "false",
                eventType.length() ? eventType.c_str() : "UNKNOWN");

  if (tamper && !alreadyTampered) {
    queueCapture(lockerId, eventType);
  }
}

void connectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("[MQTT] connecting...");

    if (mqttClient.connect(CLIENT_ID)) {
      Serial.println(" ok");

      if (mqttClient.subscribe(EVENT_TOPIC, 1)) {
        Serial.printf("[MQTT] subscribed: %s\n", EVENT_TOPIC);
      } else {
        Serial.printf("[MQTT] subscribe failed: %s\n", EVENT_TOPIC);
      }
    } else {
      Serial.printf(" failed rc=%d retry in 5s\n", mqttClient.state());
      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  Serial.println("\n=== ESP32-CAM DropLock Tamper Subscriber ===");

  if (!initSD()) {
    while (true) delay(1000);
  }
  if (!loadMqttTlsCertificates()) {
    while (true) delay(1000);
  }
  if (!initCamera()) {
    while (true) delay(1000);
  }

  connectWiFi();
  if (!syncTime()) {
    while (true) delay(1000);
  }

  mqttClient.setServer(BROKER_HOST, BROKER_PORT);
  mqttClient.setCallback(onMessage);
  mqttClient.setBufferSize(2048);
  mqttClient.setKeepAlive(60);

  connectMQTT();
}

void loop() {
  if (!mqttClient.connected()) {
    connectMQTT();
  }

  mqttClient.loop();
  processPendingCaptures();
}
