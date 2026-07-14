"""比賽環境專屬訓練機：上傳資料到 S3 → 開 EC2 跑實驗套件 → 結果回收。

安全設計：
- 只操作帶 Name=hullwatch-train 標籤的資源，絕不觸碰其他機器
- 機器不持有任何憑證：資料下載/結果上傳全走 presigned URL
- 無 inbound 規則（不開任何埠），用完 --teardown 收乾淨

用法：
    python scripts/launch_train_ec2.py            # 上傳資料 + 開機 + 跑實驗
    python scripts/launch_train_ec2.py --status   # 查狀態
    python scripts/launch_train_ec2.py --fetch    # 撈結果（跑完後）
    python scripts/launch_train_ec2.py --teardown # 終止機器
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "yangming-aws-summit-hackathon"
BUCKET = "hullwatch-exp-961190339854"
TAG = "hullwatch-train"
INSTANCE_TYPE = "c6i.4xlarge"
REPO = "https://github.com/NCHU-ICTALab/hullwatch.git"
EXPIRE = 6 * 3600

UPLOADS = [
    (DATASET / "vt_fd.csv", "data/vt_fd.csv"),
    (DATASET / "maintenance.csv", "data/maintenance.csv"),
    (ROOT / "data" / "artifacts" / "best_params_102.json", "data/best_params_102.json"),
    (ROOT / "data" / "submission" / "predictions.csv", "data/predictions.csv"),
]
RESULT_KEY = "results/results.tar.gz"
LOG_KEY = "results/train.log"


def session() -> boto3.Session:
    env = {}
    cred = ROOT.parent / ".aws" / "credentials"
    for line in cred.read_text(encoding="utf-8").splitlines():
        m = re.match(r'export (\w+)="(.+)"', line.strip())
        if m:
            env[m.group(1)] = m.group(2)
    return boto3.Session(
        aws_access_key_id=env["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=env.get("AWS_SESSION_TOKEN"),
        region_name=env.get("AWS_DEFAULT_REGION", "us-east-1"))


def user_data(gets: dict[str, str], put_url: str, log_put_url: str) -> str:
    dl = "\n".join(
        f"curl -sSf -o '{dst}' '{url}'" for dst, url in gets.items())
    return f"""#!/bin/bash
set -x
exec > /var/log/hullwatch-train.log 2>&1
# log 心跳：每 60 秒把訓練 log 推上 S3，外部可即時觀察
(while true; do curl -s -X PUT -T /var/log/hullwatch-train.log '{log_put_url}' || true; sleep 60; done) &
BEACON=$!
dnf install -y python3.11 python3.11-pip git tar libgomp
git clone {REPO} /opt/hullwatch
cd /opt/hullwatch
mkdir -p data/yangming-aws-summit-hackathon data/artifacts data/submission results
{dl}
python3.11 -m pip install -q -r requirements.txt lightgbm
python3.11 -m app.pipeline.ingest_yangming data/yangming-aws-summit-hackathon
python3.11 scripts/run_experiments.py --out results || echo EXPERIMENTS-FAILED
cp /var/log/hullwatch-train.log results/train.log || true
tar czf /tmp/results.tar.gz -C results .
curl -sSf -X PUT -T /tmp/results.tar.gz '{put_url}'
kill $BEACON || true
curl -s -X PUT -T /var/log/hullwatch-train.log '{log_put_url}' || true
echo TRAINING-COMPLETE
"""


def launch(ses: boto3.Session) -> None:
    s3 = ses.client("s3")
    try:
        s3.create_bucket(Bucket=BUCKET)
    except Exception as e:
        if "BucketAlreadyOwnedByYou" not in str(e):
            raise
    gets = {}
    for local, key in UPLOADS:
        s3.upload_file(str(local), BUCKET, key)
        dst = {"data/vt_fd.csv": "data/yangming-aws-summit-hackathon/vt_fd.csv",
               "data/maintenance.csv": "data/yangming-aws-summit-hackathon/maintenance.csv",
               "data/best_params_102.json": "data/artifacts/best_params_102.json",
               "data/predictions.csv": "data/submission/predictions.csv"}[key]
        gets[dst] = s3.generate_presigned_url("get_object",
                                              Params={"Bucket": BUCKET, "Key": key},
                                              ExpiresIn=EXPIRE)
    put_url = s3.generate_presigned_url("put_object",
                                        Params={"Bucket": BUCKET, "Key": RESULT_KEY},
                                        ExpiresIn=EXPIRE)
    log_put_url = s3.generate_presigned_url("put_object",
                                            Params={"Bucket": BUCKET, "Key": LOG_KEY},
                                            ExpiresIn=EXPIRE)
    print(f"[*] 資料已上傳 s3://{BUCKET}")

    ec2 = ses.client("ec2")
    ssm = ses.client("ssm")
    ami = ssm.get_parameter(
        Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
    )["Parameter"]["Value"]
    vpc = [v for v in ec2.describe_vpcs()["Vpcs"] if v.get("IsDefault")][0]["VpcId"]
    try:
        sg = ec2.create_security_group(GroupName=TAG, Description="hullwatch train (no inbound)",
                                       VpcId=vpc)["GroupId"]
    except Exception as e:
        if "Duplicate" not in str(e):
            raise
        sg = ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [TAG]}])["SecurityGroups"][0]["GroupId"]
    r = ec2.run_instances(
        ImageId=ami, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
        SecurityGroupIds=[sg], UserData=user_data(gets, put_url, log_put_url),
        BlockDeviceMappings=[{"DeviceName": "/dev/xvda",
                              "Ebs": {"VolumeSize": 20, "VolumeType": "gp3"}}],
        TagSpecifications=[{"ResourceType": "instance",
                            "Tags": [{"Key": "Name", "Value": TAG}]}],
        InstanceInitiatedShutdownBehavior="terminate",
    )
    iid = r["Instances"][0]["InstanceId"]
    print(f"[OK] 訓練機啟動: {iid}（{INSTANCE_TYPE}）— 結果將出現在 s3://{BUCKET}/{RESULT_KEY}")


def _mine(ec2):
    r = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [TAG]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
    return [i for res in r["Reservations"] for i in res["Instances"]]


def status(ses, show_log: bool = False) -> None:
    for i in _mine(ses.client("ec2")):
        print(i["InstanceId"], i["State"]["Name"], i["InstanceType"],
              i.get("LaunchTime", ""))
    s3 = ses.client("s3")
    try:
        h = s3.head_object(Bucket=BUCKET, Key=RESULT_KEY)
        print(f"[結果已就緒] {h['ContentLength']} bytes, {h['LastModified']}")
    except Exception:
        print("（結果尚未上傳）")
    if show_log:
        try:
            log = s3.get_object(Bucket=BUCKET, Key=LOG_KEY)["Body"].read().decode(
                "utf-8", "replace")
            print("--- train.log 尾端 ---")
            print("\n".join(log.splitlines()[-30:]))
        except Exception as e:
            print(f"（無 log：{type(e).__name__}）")


def fetch(ses) -> None:
    out = ROOT / "results_ec2"
    out.mkdir(exist_ok=True)
    tgz = out / "results.tar.gz"
    ses.client("s3").download_file(BUCKET, RESULT_KEY, str(tgz))
    with tarfile.open(tgz) as tf:
        tf.extractall(out)
    print(f"[OK] 結果解到 {out}")


def teardown(ses) -> None:
    ec2 = ses.client("ec2")
    ids = [i["InstanceId"] for i in _mine(ec2)]
    if ids:
        ec2.terminate_instances(InstanceIds=ids)
        print("[OK] 已終止:", ids)
    else:
        print("（無 hullwatch-train 機器）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--teardown", action="store_true")
    args = ap.parse_args()
    ses = session()
    if args.status or args.log:
        status(ses, show_log=args.log)
    elif args.fetch:
        fetch(ses)
    elif args.teardown:
        teardown(ses)
    else:
        launch(ses)
