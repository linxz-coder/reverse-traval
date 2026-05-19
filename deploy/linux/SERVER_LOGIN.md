# 服务器登录方式

这个项目服务器目前用 GitHub 账号 `linxz-coder` 的公开 SSH Key 登录。

## 服务器信息

- SSH 别名：`reverse-travel-server` 或 `hotel-server`
- 服务器公网 IP：`43.128.25.63`
- SSH 用户：`ubuntu`
- 本机私钥：`~/.ssh/id_ed25519_github`
- GitHub 公钥地址：`https://github.com/linxz-coder.keys`

## 从这台 Mac 登录

本机 `/Users/linxiaozhong/.ssh/config` 已经写入：

```sshconfig
Host reverse-travel-server hotel-server
    HostName 43.128.25.63
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519_github
    IdentitiesOnly yes
```

以后可以直接用下面任意一个命令登录：

```bash
ssh reverse-travel-server
ssh hotel-server
```

等价的完整命令是：

```bash
ssh -i ~/.ssh/id_ed25519_github ubuntu@43.128.25.63
```

## 在新服务器上授权这种登录方式

如果以后换服务器，先用密码或控制台登录到服务器的 `ubuntu` 用户，然后执行：

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
curl -fsSL https://github.com/linxz-coder.keys >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

如果必须加到 `root` 用户，不能用 `sudo echo ... >> /root/.ssh/authorized_keys`，因为重定向不归 `sudo` 管。要用：

```bash
sudo mkdir -p /root/.ssh
curl -fsSL https://github.com/linxz-coder.keys | sudo tee -a /root/.ssh/authorized_keys >/dev/null
sudo chmod 700 /root/.ssh
sudo chmod 600 /root/.ssh/authorized_keys
```

不要上传或粘贴私钥。服务器只需要使用 GitHub 公钥地址 `https://github.com/linxz-coder.keys`。
