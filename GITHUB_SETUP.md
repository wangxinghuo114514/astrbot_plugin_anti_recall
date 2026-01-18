# GitHub 仓库设置指南

## 前置条件

1. 确保你已经安装了 Git
2. 确保你有一个 GitHub 账号

## 步骤 1：在 GitHub 上创建新仓库

1. 访问 https://github.com/new
2. 仓库名称填写：`astrbot_plugin_anti_recall`
3. 设置为 Public 或 Private（根据你的需求）
4. 不要初始化 README、.gitignore 或 license（我们已经有了）
5. 点击 "Create repository"

## 步骤 2：关联远程仓库

在插件目录下执行以下命令：

```bash
cd /www/dk_project/dk_app/astrbot/astrbot_KfCC/data/plugins/astrbot_plugin_anti_recall
git remote add origin https://github.com/wangxinghuo/astrbot_plugin_anti_recall.git
```

## 步骤 3：推送代码到 GitHub

```bash
git branch -M main
git push -u origin main
```

如果遇到认证问题，可能需要使用 SSH：

```bash
git remote set-url origin git@github.com:wangxinghuo/astrbot_plugin_anti_recall.git
git push -u origin main
```

## 步骤 4：验证

访问 https://github.com/wangxinghuo/astrbot_plugin_anti_recall 查看你的代码是否已成功上传。

## 后续操作

### 添加 GitHub Actions（可选）

如果需要自动化测试或 CI/CD，可以创建 `.github/workflows/` 目录并添加相应的工作流文件。

### 添加贡献指南（可选）

创建 `CONTRIBUTING.md` 文件，说明如何为项目做贡献。

### 添加行为准则（可选）

创建 `CODE_OF_CONDUCT.md` 文件，制定社区行为准则。

## 注意事项

1. 确保 GitHub 仓库的描述和标签与 `metadata.yaml` 中的信息一致
2. 定期更新版本号和 CHANGELOG
3. 及时响应 Issue 和 Pull Request
4. 遵循 AstrBot 插件开发规范

## 许可证

本项目采用 MIT 许可证，详见 LICENSE 文件。