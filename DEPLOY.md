# Google Cloud Run 部署指南

本文档提供将股票分时数据查询服务部署到 Google Cloud Run 的步骤。

## 前提条件

1. 安装 [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
2. 拥有 Google Cloud 账号并创建项目
3. 启用 Cloud Run API 和 Container Registry API
4. 安装 Docker

## 部署步骤

### 1. 登录 Google Cloud

```bash
gcloud auth login
```

### 2. 设置项目 ID

```bash
gcloud config set project YOUR_PROJECT_ID
```

### 3. 构建 Docker 镜像

```bash
# 在项目根目录下执行
docker build -t gcr.io/YOUR_PROJECT_ID/stock-data-api .
```

### 4. 推送镜像到 Google Container Registry

```bash
# 配置 Docker 使用 gcloud 作为凭证助手
gcloud auth configure-docker

# 推送镜像
docker push gcr.io/YOUR_PROJECT_ID/stock-data-api
```

### 5. 部署到 Cloud Run

```bash
gcloud run deploy stock-data-api \
  --image gcr.io/YOUR_PROJECT_ID/stock-data-api \
  --platform managed \
  --region asia-east1 \
  --allow-unauthenticated \
  --set-env-vars="SUPABASE_URL=YOUR_SUPABASE_URL,SUPABASE_KEY=YOUR_SUPABASE_KEY"
```

> 注意：`--allow-unauthenticated` 参数允许未经身份验证的访问。如果您的 API 需要身份验证，请移除此参数。

### 6. 访问服务

部署完成后，Cloud Run 将提供一个 URL，您可以通过该 URL 访问您的 API。

## 环境变量配置

在 Cloud Run 中，您需要设置以下环境变量：

- `SUPABASE_URL`: 您的 Supabase 项目 URL
- `SUPABASE_KEY`: 您的 Supabase 项目 API 密钥

您可以在部署命令中使用 `--set-env-vars` 参数设置这些变量，或者在 Google Cloud Console 中配置。

## 自动化部署

您可以使用 GitHub Actions 或 Cloud Build 设置 CI/CD 流水线，实现代码更新后自动部署到 Cloud Run。

### 从 GitHub 仓库直接部署

1. 首先，将项目上传到 GitHub 仓库：

```bash
# 初始化 Git 仓库
git init

# 添加所有文件
git add .

# 提交更改
git commit -m "初始提交"

# 添加远程仓库
git remote add origin https://github.com/你的用户名/你的仓库名.git

# 推送到 GitHub
git push -u origin main
```

2. 在 Google Cloud Console 中连接 GitHub 仓库：
   - 打开 Google Cloud Console
   - 导航到 Cloud Build > 触发器
   - 点击「连接仓库」
   - 选择 GitHub 并按照提示授权
   - 选择您的仓库

3. 创建 Cloud Build 触发器：
   - 名称：stock-data-api-trigger
   - 事件：推送到分支
   - 分支：^main$
   - 配置文件：cloudbuild.yaml
   - 替代变量：
     - _SUPABASE_URL: 您的 Supabase URL
     - _SUPABASE_KEY: 您的 Supabase Key

现在，每当您推送代码到 GitHub 仓库的 main 分支时，Cloud Build 将自动构建并部署到 Cloud Run。

### 使用 GitHub Actions 自动部署

项目已包含 GitHub Actions 工作流配置文件 `.github/workflows/deploy-to-cloud-run.yml`，可以自动部署到 Cloud Run。

1. 在 GitHub 仓库中设置以下 Secrets：
   - `GCP_PROJECT_ID`: 您的 Google Cloud 项目 ID
   - `GCP_SA_KEY`: 您的 Google Cloud 服务账号密钥（JSON 格式，Base64 编码）
   - `SUPABASE_URL`: 您的 Supabase URL
   - `SUPABASE_KEY`: 您的 Supabase Key

2. 创建服务账号并获取密钥：

```bash
# 创建服务账号
gcloud iam service-accounts create github-actions-sa \
  --display-name="GitHub Actions Service Account"

# 授予必要权限
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.admin"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

# 创建并下载密钥
gcloud iam service-accounts keys create key.json \
  --iam-account=github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com

# 将密钥转换为 Base64 格式
cat key.json | base64
```

3. 将 Base64 编码后的密钥添加到 GitHub Secrets 中的 `GCP_SA_KEY`。

现在，每当您推送代码到 GitHub 仓库的 main 分支时，GitHub Actions 将自动构建并部署到 Cloud Run。

### 使用 Cloud Build 自动部署

1. 在项目根目录创建 `cloudbuild.yaml` 文件（已创建）：

```yaml
steps:
  # 构建镜像
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', 'gcr.io/$PROJECT_ID/stock-data-api', '.']
  
  # 推送镜像
  - name: 'gcr.io/cloud-builders/docker'
    args: ['push', 'gcr.io/$PROJECT_ID/stock-data-api']
  
  # 部署到 Cloud Run
  - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
    entrypoint: 'gcloud'
    args:
      - 'run'
      - 'deploy'
      - 'stock-data-api'
      - '--image'
      - 'gcr.io/$PROJECT_ID/stock-data-api'
      - '--platform'
      - 'managed'
      - '--region'
      - 'asia-east1'
      - '--allow-unauthenticated'
      - '--set-env-vars'
      - 'SUPABASE_URL=${_SUPABASE_URL},SUPABASE_KEY=${_SUPABASE_KEY}'

images:
  - 'gcr.io/$PROJECT_ID/stock-data-api'
```

2. 在 Google Cloud Console 中设置 Cloud Build 触发器，并配置 `_SUPABASE_URL` 和 `_SUPABASE_KEY` 作为替代变量。

## 监控和日志

- 您可以在 Google Cloud Console 中查看 Cloud Run 服务的监控指标和日志。
- 使用 `gcloud run services logs read stock-data-api` 命令查看服务日志。

## 扩展和优化

- Cloud Run 会根据流量自动扩展，您可以设置最小和最大实例数。
- 考虑使用 Cloud SQL 或其他托管数据库服务，而不是直接连接到 Supabase。
- 使用 Cloud Secret Manager 管理敏感信息，如 API 密钥。