# 证书目录（开发用，**私钥不入库**）

本目录里的 `*.pem` 由脚本自动生成，**已经被 `.gitignore` 排除**，不会进 Git：

| 文件 | 用途 | 是否入库 |
|---|---|---|
| `ca.pem` | 自签开发 CA，手机安装它之后 https 不再报警；Android App 内置信任 | 否（可重新生成） |
| `ca-key.pem` | CA 私钥（签发服务器证书用） | **绝不入库** |
| `server.pem` | 服务器证书（CA 签发，SAN 含 localhost/127.0.0.1/本机局域网 IP） | 否（可重新生成） |
| `server-key.pem` | 服务器私钥 | **绝不入库** |

## 在任意一台电脑上重新生成

```powershell
cd <仓库根>
python tools/make_dev_cert.py          # 已有可用证书会复用，局域网 IP 变了会自动重签
python tools/make_dev_cert.py --force  # 强制重签
python tools/make_dev_cert.py --detect-only   # 只看本机局域网 IP
```

生成后：
- 电脑起 HTTPS：`powershell -ExecutionPolicy Bypass -File scripts\start-https.ps1`
- 手机下载 CA：`https://<电脑IP>:8443/api/dev/ca.pem`（安装步骤见 `docs/PHONE_GUIDE.md` §3.2）
- Android App 需要把 CA 复制成 `mobile/android/app/src/main/res/raw/lb_dev_ca.pem`：
  `python mobile/scripts/apply-mobile-config.py <电脑IP>:8443` 会自动做（见 `mobile/README.md`）。

> 安全提醒：私钥只在本机开发使用，不要发到群里/传网盘/提交到 Git；正式发布要换成有公网证书的域名，
> 并删掉 APK 里对 `@raw/lb_dev_ca` 的信任（`mobile/README.md` §7）。
