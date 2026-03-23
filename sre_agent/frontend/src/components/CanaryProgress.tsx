import { List, Progress, Tag } from "antd";

type CanaryBatch = {
  batch: string;
  progress: number;
  status: string;
};

type CanaryProgressProps = {
  batches: CanaryBatch[];
};

function CanaryProgress({ batches }: CanaryProgressProps) {
  return (
    <List
      dataSource={batches}
      renderItem={(item) => (
        <List.Item>
          <List.Item.Meta title={item.batch} description={<Progress percent={item.progress} strokeColor="#0f766e" />} />
          <Tag color={item.status === "validating" ? "gold" : item.status === "pending" ? "default" : "green"}>{item.status}</Tag>
        </List.Item>
      )}
    />
  );
}

export default CanaryProgress;
