# RoCEv2 ECN Checklist

Enable ECN consistently on all leaf-spine switches serving RDMA traffic.

RoCEv2 ECN 配置需要在全网交换机上保持一致，避免 RDMA 流量出现拥塞扩散。

Validate PFC, ECN marking thresholds, and congestion telemetry together.

When GPU inference latency rises together with RoCE retransmissions, verify switch queue thresholds before restarting workloads.
