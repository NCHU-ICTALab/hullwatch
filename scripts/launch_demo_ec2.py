"""一鍵開 EC2 demo 機：安全群組 + AL2023 + user-data 自動部署 hullwatch。

用法（Learner Lab 或比賽環境皆可）：
    python scripts/launch_demo_ec2.py            # 開機 + 自動部署
    python scripts/launch_demo_ec2.py --status   # 查狀態與公網 IP
    python scripts/launch_demo_ec2.py --teardown # 收攤（終止機器）

機器開機後 user-data 會自動：裝 docker/git → clone GitHub repo → build → run。
從開機到可訪問約 6–10 分鐘（pip install 在容器 build 內）。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import boto3

REGION = os.environ.get("HW_DEPLOY_REGION", "us-east-1")
REPO_URL = "https://github.com/NCHU-ICTALab/hullwatch.git"
SG_NAME = "hullwatch-demo"
TAG_NAME = "hullwatch-demo"
INSTANCE_TYPE = "t3.medium"

# Learner Lab 憑證檔（標準位置有設定時不覆蓋）
_cred = Path(__file__).resolve().parents[2] / ".aws" / "credentials"
if _cred.exists() and "AWS_SHARED_CREDENTIALS_FILE" not in os.environ:
    os.environ["AWS_SHARED_CREDENTIALS_FILE"] = str(_cred)

USER_DATA = f"""#!/bin/bash
set -x
dnf install -y docker git
systemctl enable --now docker
git clone {REPO_URL} /opt/hullwatch
cd /opt/hullwatch
docker build -t hullwatch .
docker run -d --name hw -p 8000:8000 --restart unless-stopped hullwatch
"""


def _clients():
    return boto3.client("ec2", region_name=REGION), boto3.client("ssm", region_name=REGION)


def _find_instances(ec2):
    r = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [TAG_NAME]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
    ])
    return [i for res in r["Reservations"] for i in res["Instances"]]


def launch() -> None:
    ec2, ssm = _clients()
    existing = _find_instances(ec2)
    if existing:
        print(f"[!] 已有 {TAG_NAME} 機器（{existing[0]['InstanceId']}），先 --teardown 或用 --status 查 IP")
        return
    vpc = [v for v in ec2.describe_vpcs()["Vpcs"] if v.get("IsDefault")][0]["VpcId"]
    ami = ssm.get_parameter(
        Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
    )["Parameter"]["Value"]
    try:
        sg = ec2.create_security_group(
            GroupName=SG_NAME, Description="hullwatch demo port 8000", VpcId=vpc)["GroupId"]
        ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 8000, "ToPort": 8000,
             "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    except Exception as e:
        if "Duplicate" not in str(e):
            raise
        sg = ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [SG_NAME]}])["SecurityGroups"][0]["GroupId"]
    r = ec2.run_instances(
        ImageId=ami, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
        SecurityGroupIds=[sg], UserData=USER_DATA,
        BlockDeviceMappings=[{"DeviceName": "/dev/xvda",
                              "Ebs": {"VolumeSize": 16, "VolumeType": "gp3"}}],
        TagSpecifications=[{"ResourceType": "instance",
                            "Tags": [{"Key": "Name", "Value": TAG_NAME}]}],
    )
    iid = r["Instances"][0]["InstanceId"]
    print(f"[*] 機器啟動中: {iid}")
    ec2.get_waiter("instance_running").wait(InstanceIds=[iid])
    ip = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0].get("PublicIpAddress")
    print(f"[OK] running，公網 IP: {ip}")
    print(f"    約 6–10 分鐘後訪問: http://{ip}:8000  （health: /api/health）")


def status() -> None:
    ec2, _ = _clients()
    for i in _find_instances(ec2):
        print(i["InstanceId"], i["State"]["Name"], i.get("PublicIpAddress", "-"),
              f"http://{i.get('PublicIpAddress')}:8000" if i.get("PublicIpAddress") else "")
    else:
        if not _find_instances(ec2):
            print("（無 hullwatch-demo 機器）")


def teardown() -> None:
    ec2, _ = _clients()
    ids = [i["InstanceId"] for i in _find_instances(ec2)]
    if not ids:
        print("（無機器可終止）")
        return
    ec2.terminate_instances(InstanceIds=ids)
    print("[OK] 已送出終止:", ids)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--teardown", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
    elif args.teardown:
        teardown()
    else:
        launch()
