# 拍摄指导 · Android App（Capacitor 套壳）

`mobile/` 是把 `web/` 前端用 **Capacitor 6** 套壳成可安装 Android App 的工程。
App 本身只是 WebView 壳：**界面和逻辑全在 `web/`（`cap sync` 时原样拷进 APK 的 `assets/public/`）**，
后端（FastAPI + 引擎）仍然跑在电脑上，手机通过同一 Wi-Fi 访问。

```
mobile/
├── package.json              # @capacitor/core / cli / android 6.x
├── capacitor.config.json     # appId / appName / webDir=../web / androidScheme=https
├── scripts/
│   ├── apply-mobile-config.py  # sync 后写回 API 地址 + 校验 index.html 加载顺序 + 拷 CA
│   └── build-debug.ps1         # 一键（便携工具链）构建 debug APK
├── android/                  # Capacitor 生成的 Android 工程（Gradle）
│   └── app/src/main/
│       ├── AndroidManifest.xml            # CAMERA 权限 + 网络安全配置
│       ├── assets/public/                 # cap sync 从 web/ 拷来的前端（app-config.js 被覆写）
│       └── res/
│           ├── xml/network_security_config.xml  # 信任系统 CA + 打包的 lb_dev_ca
│           └── raw/lb_dev_ca.pem                # = certs/ca.pem（开发用自签 CA）
└── shooting-guide-debug.apk  # 构建产物（若已成功构建）
```

## 1. 先决条件：电脑侧要跑 HTTPS 后端

APK **不内置后端**。手机打开 App 后，前端所有 `/api/*` 请求都会发到 `https://172.20.10.7:8443`
（这个地址写在 APK 内的 `app-config.js`，见 §4）。

```powershell
# 仓库根目录执行（会确保证书存在、探测局域网 IP、起 8443 的 HTTPS 服务）
powershell -ExecutionPolicy Bypass -File scripts\start-https.ps1
```

- 手机和电脑必须连**同一个 Wi-Fi**；
- 电脑防火墙需要放行 8443/TCP（Windows 首次启动 uvicorn 时会弹「允许访问」）；
- 手机浏览器先打开 `https://<电脑IP>:8443` 应能看到页面（自签证书会告警，App 内已内置 CA 信任，无需手动装证书）。

## 2. 直接安装已构建的 debug APK（推荐给答辩/试用）

APK 路径：

```
D:\videoshoot\mobile\shooting-guide-debug.apk
```

- 大小 **3,799,027 字节（3.62 MiB）**，SHA-256
  `574CD49408431E0036D70B8A60509BC26DC006D23F6EB2A161290ECE29635AAB`；
- 包名 `com.learnbuddy.shootingguide`，应用名「拍摄指导」，launcher activity
  `com.learnbuddy.shootingguide.MainActivity`，minSdk 22 / targetSdk 34 / compileSdk 34；
- 已用 `apksigner verify --verbose` 校验通过（v1 + v2 签名，1 个签名者：`CN=Android Debug`），
  详见 §10 验证记录；
- 这是 **debug 签名** APK，只能用于本机调试/内部分发，不能上架应用商店；
- 安装方式（任选）：
  1. 数据线 + adb（`platform-tools` 在 `_tools\android-sdk\platform-tools\`）：
     ```powershell
     & D:\videoshoot\_tools\android-sdk\platform-tools\adb.exe install -r D:\videoshoot\mobile\shooting-guide-debug.apk
     ```
  2. 把 APK 发到手机（微信文件传输/数据线拷贝/局域网下载），在手机上点开安装；
     需要允许「安装未知来源应用」（MIUI/EMUI 可能还要在文件管理器里单独放行）。

## 3. 用 Android Studio 打开 / 构建

1. Android Studio（Hedgehog 以上，自带 JDK 17）→ **Open** → 选 `D:\videoshoot\mobile\android`
   （注意：选 `android/` 子目录，不要选 `mobile/` 根目录）。
2. 首次打开会提示 SDK 路径；本机便携 SDK 在 `D:\videoshoot\_tools\android-sdk`，也可用 Studio 自带的。
3. 等 Gradle sync 完成后：
   - 菜单 `Build > Build Bundle(s) / APK(s) > Build APK(s)`，产物在
     `mobile/android/app/build/outputs/apk/debug/app-debug.apk`；
   - 或命令行（工程根 `mobile/android`）：
     ```bat
     gradlew.bat assembleDebug
     ```
4. 真机调试：手机开 USB 调试，`adb devices` 能看到设备后点 Studio 的 Run ▶。

## 4. APK 里的 API 地址怎么改（重要）

前端读取 `window.__LB_API_BASE__` 作为所有 `/api/*` 的基址
（`web/index.html` 先加载 `app-config.js`，再加载 `app.js`）。
`cap sync` 会把 `web/app-config.js`（默认空值）原样拷进
`android/app/src/main/assets/public/app-config.js`，所以 **每次 sync 之后必须覆写它**：

```powershell
cd D:\videoshoot\mobile
python scripts\apply-mobile-config.py 172.20.10.7:8443
# 或：python scripts\apply-mobile-config.py 192.168.1.23:8443
# 或：python scripts\apply-mobile-config.py https://192.168.1.23:8443
```

脚本会：
1. 把 `assets/public/app-config.js` 写成 `window.__LB_API_BASE__ = "https://<host>:8443";`
2. 校验 `assets/public/index.html` 里 `app-config.js` 确实在 `app.js` 之前加载（否则报错退出）；
3. 把 `certs/ca.pem` 复制到 `res/raw/lb_dev_ca.pem`（幂等）。

然后重建 APK：

```powershell
cd D:\videoshoot\mobile\android
gradlew.bat assembleDebug
```

> 也可以临时在 App 打开的 URL 上加 `?api=https://...` 调试，但 WebView 里没有地址栏，
> 常规做法还是改这里重新打包。

**一键完成 sync + 写回地址 + 构建 + 签名校验**（推荐，使用便携工具链）：

```powershell
powershell -ExecutionPolicy Bypass -File D:\videoshoot\mobile\scripts\build-debug.ps1
# 指定别的后端地址：
powershell -ExecutionPolicy Bypass -File D:\videoshoot\mobile\scripts\build-debug.ps1 -ApiBase 192.168.1.23:8443
```

## 5. 证书信任（为什么不用手动装 CA）

`AndroidManifest.xml` 的 `<application>` 上挂了
`android:networkSecurityConfig="@xml/network_security_config"`，其内容：

```xml
<base-config>
  <trust-anchors>
    <certificates src="system" />          <!-- 正常公网 HTTPS -->
    <certificates src="@raw/lb_dev_ca" />  <!-- certs/ca.pem，局域网自签 CA -->
  </trust-anchors>
</base-config>
```

- `res/raw/lb_dev_ca.pem` 就是 `certs/ca.pem`（CA：`LearnBuddy Dev CA`，有效期至 2036-11-20）；
- 服务端证书 `certs/server.pem` 由该 CA 签发，SAN 含 `localhost / 127.0.0.1 / 172.20.10.7`，
  所以 WebView 访问 `https://172.20.10.7:8443` 不会报证书错误；
- **发布给外部用户前**要删掉 `<certificates src="@raw/lb_dev_ca" />`（信任自签 CA 是开发期便利，
  不能带进正式包），并把 API 换成有公网证书的域名（见 §7）。

## 6. 相机权限说明

- `AndroidManifest.xml` 声明了：
  - `<uses-permission android:name="android.permission.CAMERA" />` —— `getUserMedia` 必需；
  - `<uses-feature android:name="android.hardware.camera" android:required="false" />` ——
    没有相机的设备也允许安装，此时前端自动进「演示模式」。
- 页面源是 `https://localhost`（`server.androidScheme: "https"`），属于**安全上下文**，
  WebView 的 `getUserMedia` 可以工作；Capacitor 的 `WebChromeClient` 会代理网页的
  权限申请，首次点「给建议」/启动相机时系统会弹「允许拍摄照片和录制视频」——选**允许**。
- 若拒绝权限：前端会显示「正在启动相机…」失败后进演示模式（可点演示场景继续演示，
  `/api/suggest` 仍用真实 demo 帧走真实特征提取）；可在系统「设置 → 应用 → 拍摄指导 → 权限」里重新打开。
- 若真机上拿不到相机，优先检查：① 系统相机权限是否给了；② 是否从设置里禁用了相机。

## 7. 签名 / 发布注意

- **debug 包**：Gradle 自动用 `~/.android/debug.keystore` 签名，可直接装；
  默认有效期有限、不可上架。
- **release 包**（要分发给别人 / 上架）：
  1. 生成 keystore（示例）：
     ```powershell
     & "$env:JAVA_HOME\bin\keytool.exe" -genkeypair -v -keystore D:\videoshoot\mobile\release.keystore `
       -alias shootingguide -keyalg RSA -keysize 2048 -validity 10000
     ```
  2. 在 `android/app/build.gradle` 里配 `signingConfigs.release`（keystore 路径/密码建议走
     `gradle.properties` 或环境变量，**不要把密码提交进仓库**）；
  3. `gradlew.bat assembleRelease` → `app/build/outputs/apk/release/app-release.apk`；
  4. `apksigner` 校验 + `zipalign`（AGP 已自动对齐）：
     ```powershell
     & D:\videoshoot\_tools\android-sdk\build-tools\34.0.0\apksigner.bat verify --verbose app-release.apk
     ```
  5. 上架 Google Play 需要 AAB（`bundleRelease`）+ targetSdk 满足当年要求（当前 34）+ 隐私政策
     （App 使用相机，需在 Play 控制台声明相机权限用途）。
- 发布前检查：移除 `@raw/lb_dev_ca` 信任；`app-config.js` 改公网 HTTPS 地址；
  `android:allowBackup` 按需关闭；包名 `com.learnbuddy.shootingguide` 一旦上架不可改。

## 8. 本机便携工具链（为什么仓库里没有 node/JDK/SDK）

本机没有系统级 node/npm/JDK/Android SDK，全部装在 `_tools/`（不写系统目录、不改系统环境变量）：

| 工具 | 路径 | 版本 |
| --- | --- | --- |
| Node.js | `_tools\node-v20.18.0-win-x64\` | v20.18.0（npm 10.8.2） |
| Temurin JDK | `_tools\jdk-17.0.21.1+2\` | 17.0.21.1+2 |
| Android SDK | `_tools\android-sdk\` | cmdline-tools 12.0 / platform-tools 37.0.3 / platforms;android-34 / build-tools;34.0.0 |
| Gradle 缓存 | `_tools\gradle-home\` | Gradle 8.2.1（由 wrapper 下载） |

命令行构建完整环境变量（`scripts\build-debug.ps1` 已封装）：

```powershell
$env:JAVA_HOME      = "D:\videoshoot\_tools\jdk-17.0.21.1+2"
$env:ANDROID_HOME   = "D:\videoshoot\_tools\android-sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:GRADLE_USER_HOME = "D:\videoshoot\_tools\gradle-home"
$env:PATH = "D:\videoshoot\_tools\node-v20.18.0-win-x64;$env:JAVA_HOME\bin;$env:PATH"
cd D:\videoshoot\mobile\android
.\gradlew.bat --no-daemon assembleDebug
```

`android/local.properties` 里已写死 `sdk.dir=D:\\videoshoot\\_tools\\android-sdk`；
换机器时删掉它（Android Studio 会重新生成）或改成自己的 SDK 路径。

## 9. 已知限制（诚实说明）

1. **APK 不内置后端**：App 只是壳，必须电脑先跑 `scripts\start-https.ps1`，且手机与电脑同一 Wi-Fi。
   电脑没起服务时 App 会显示「连不上本地服务」（顶部状态），此时只能看壳/演示模式骨架。
2. **API 地址写死在 APK 里**：默认 `https://172.20.10.7:8443`（本机 LAN IP）。
   换网络/换电脑后 IP 变了，App 连不上，需要改 `app-config.js` 重新构建（见 §4）；
   可在 `web/app.js` 里改用 `?api=` 查询参数调试，但 WebView 无地址栏，命令行改更实际。
3. **开发用自签 CA**：APK 内置信任 `certs/ca.pem`；仅开发期适用，正式发布必须移除。
4. **未在真机实测**：本机没有 Android SDK 设备/模拟器（不支持嵌套虚拟化），APK 只做了
   静态校验（apksigner / aapt2）与安装说明，未做真机启动、相机权限弹窗、真实 Wi-Fi 联调；
   首次真机验证建议按 §2 安装后看：能否打开界面 → 顶部状态是否变「服务就绪」→ 相机权限弹窗 → 演示模式。
5. **不内置 ffmpeg**：粗剪面板的「下载 FFmpeg 脚本」只是下发电脑端命令文本，App 内不做视频转码。
6. **PWA Service Worker**：`web/sw.js` 在 WebView 里不注册（App 资源本身已离线内置，
   不需要 SW）；不要指望浏览器「添加到主屏幕」那套离线缓存策略。
7. **release 签名未配置**：当前只产出 debug APK；要发布按 §7 自己生成 keystore 并配置签名。
8. **建议 Android 7.0（API 24）及以上**：`network_security_config`（信任打包的自签 CA）从 API 24
   才开始生效；minSdk 虽为 22，但 Android 5.x/6.x 上 WebView 会不信任 `https://172.20.10.7:8443`
   的证书，且旧 WebView 对现代 JS/`getUserMedia` 支持也较差。演示/答辩用手机基本都 ≥ Android 8。

## 10. 验证记录（本机实际构建，2026-11-23）

构建命令（`mobile/android` 下，环境变量见 §8）：

```powershell
.\gradlew.bat --no-daemon assembleDebug
# BUILD SUCCESSFUL in 2m 4s / 82 actionable tasks: 82 executed
# 产物 android\app\build\outputs\apk\debug\app-debug.apk (3,799,027 字节)
# 拷到 mobile\shooting-guide-debug.apk
```

**签名校验**（`build-tools\34.0.0\apksigner.bat verify --verbose`）：

```
Verifies
Verified using v1 scheme (JAR signing): true
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): false
Number of signers: 1
Signer #1 certificate DN: C=US, O=Android, CN=Android Debug
```

**包信息**（`aapt2 dump badging` 摘要）：

```
package: name='com.learnbuddy.shootingguide' versionCode='1' versionName='1.0'
  platformBuildVersionName='14' compileSdkVersion='34'
sdkVersion:'22'
targetSdkVersion:'34'
uses-permission: name='android.permission.INTERNET'
uses-permission: name='android.permission.CAMERA'
application-label:'拍摄指导'
launchable-activity: name='com.learnbuddy.shootingguide.MainActivity'  label='拍摄指导'
  uses-feature-not-required: name='android.hardware.camera'
```

**其他打包内容校验**：

- `aapt2 dump xmltree AndroidManifest.xml`：`android:networkSecurityConfig=@0x7f110002`、
  `android.permission.CAMERA`、`android.hardware.camera` 均在合并后清单里；
- `aapt2 dump xmltree res/xml/network_security_config.xml`：`base-config/trust-anchors` 下
  `certificates src="system"` + `certificates src=@0x7f0d0000`（即 `@raw/lb_dev_ca`）；
- APK 内 `assets/public/` 含 index.html / app.js / styles.css / app-config.js 等全部前端文件，
  且 `assets/public/app-config.js` = `window.__LB_API_BASE__ = "https://172.20.10.7:8443";`；
- APK 内 `res/raw/lb_dev_ca.pem` 与 `certs/ca.pem` **逐字节一致**（1208 字节）。

> 构建环境备注：本机 gradle-wrapper 用 Java 直连 `services.gradle.org` 会卡在 0 字节，
> 因此用 python 把 `gradle-8.2.1-all.zip`（193MB）预下载并解压到
> `_tools\gradle-home\wrapper\dists\gradle-8.2.1-all\d8pvvlun5bx6sdtwqhf8y9z4b\`
> 并创建 `.ok` 标记（只解压 bin/lib，跳过超 MAX_PATH 的 docs/src）。换一台正常联网的机器
> 直接 `gradlew.bat` 即可，无需这套处理。
