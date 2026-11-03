# SSH 密钥登录配置步骤（以 10.0.81.89 为例）

> 适用场景：用 SSH 公钥免密登录 Linux 服务器，替代密码登录，更安全也省事。
> 示例服务器：`10.0.81.89`，示例登录用户：`root`（实际改成你的用户名）。
> 客户端：Windows（Git Bash）/ Linux / macOS 通用，下面分别给命令。

---

## 一、在客户端生成密钥对

### Windows（Git Bash，推荐）
```bash
# 一路回车即可；如已有密钥可跳过 生成的文件是在 E:\ai_space 目录下
ssh-keygen -t ed25519 -f /e/ai_space/id_81_89 -C "root@vastship-81.89" -N ""
# 查看 E:\ai_space 目录下 密钥文件  cat id_81_89.pub
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILoKShkJg8rZBJIr42N6ucokjpJyhjBUNmlyiA/oGyzT root@vastship-81.89

# 把  cat id_81_89.pub 密钥上传到服务器上去 /root/.ssh 使用命令追加 ~/.ssh/authorized_keys  文件里面去
 echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILoKShkJg8rZBJIr42N6ucokjpJyhjBUNmlyiA/oGyzT root@vastship-81.89" >> ~/.ssh/authorized_keys
 
   ```

