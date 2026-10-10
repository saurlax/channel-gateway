import assert from "node:assert/strict";
import test from "node:test";
import { resolveLocale, translate, translateDiagnostic } from "../src/i18n/locale.ts";

test("browser preference order, language variants and fallback", () => {
  assert.equal(resolveLocale(["zh-CN", "en-US"]), "zh-CN");
  assert.equal(resolveLocale(["en-GB", "zh-CN"]), "en");
  assert.equal(resolveLocale(["fr-FR", "ZH-hans", "en"]), "zh-CN");
  assert.equal(resolveLocale(["zh-TW"]), "zh-CN");
  assert.equal(resolveLocale(["de-DE", "ja-JP"]), "en");
  assert.equal(resolveLocale([]), "en");
});

test("UI translation preserves user data and literal replacement characters", () => {
  const name = "Connections $& {name} 团队";
  assert.equal(translate("zh-CN", "Enable {name}", { name }), `启用 ${name}`);
  assert.equal(translate("en", "Enable {name}", { name }), `Enable ${name}`);
  assert.equal(translate("en", "Connections"), "Connections");
  assert.equal(translate("zh-CN", "Connections"), "连接");
});

test("known diagnostics localize while upstream details remain readable", () => {
  assert.equal(translateDiagnostic("zh-CN", "Administrator sign-in required"), "请登录管理员账号");
  assert.equal(
    translateDiagnostic("zh-CN", "Value error, Group replies require administrator approval"),
    "群聊回复需要管理员审核",
  );
  assert.equal(
    translateDiagnostic("zh-CN", "String should have at least 16 characters"),
    "文本至少需要 16 个字符",
  );
  assert.equal(translateDiagnostic("zh-CN", "Request failed (503)."), "请求失败（503）。");
  assert.equal(
    translateDiagnostic("zh-CN", "Field required; Field required"),
    "必填字段；必填字段",
  );
  assert.equal(
    translateDiagnostic("zh-CN", "Host-specific diagnostic"),
    "Host-specific diagnostic",
  );
  assert.equal(translateDiagnostic("zh-CN", "constructor"), "constructor");
  assert.equal(translateDiagnostic("en", "Route permission revoked"), "Route permission revoked");
});

test("protocol status and channel labels follow the selected locale", () => {
  assert.equal(translate("zh-CN", "OneBot forward HTTP"), "OneBot 正向 HTTP");
  assert.equal(translateDiagnostic("en", "callback_ready"), "Callback ready");
  assert.equal(translateDiagnostic("zh-CN", "login_required"), "需要登录");
  assert.equal(
    translateDiagnostic("en", "weixin_login_need_verifycode"),
    "Enter the verification code",
  );
  assert.equal(translateDiagnostic("zh-CN", "weixin_login_confirmed"), "配对成功");
});
