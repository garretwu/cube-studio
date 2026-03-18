import { useEffect, useState } from "react";
import { Button, Card, Input, List, Space, Tag, Typography } from "antd";

import { apiClient } from "../api/client";
import type { KnowledgeDocument } from "../api/types";

function KnowledgePage() {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [query, setQuery] = useState("RoCEv2 ECN 配置");

  useEffect(() => {
    void apiClient.getKnowledgeSources().then(setDocuments);
  }, []);

  return (
    <div className="page-grid">
      <Card className="hero-card">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Title level={2} style={{ margin: 0 }}>
            Knowledge Browser
          </Typography.Title>
          <Space.Compact style={{ width: "100%" }}>
            <Input.Search
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onSearch={(value) => void apiClient.searchKnowledge(value).then(setDocuments)}
              enterButton
            />
            <Button>Upload</Button>
          </Space.Compact>
        </Space>
      </Card>
      <Card className="panel-card" title="Documents">
        <List
          dataSource={documents}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space wrap>
                    <Typography.Text strong>{item.title}</Typography.Text>
                    <Tag>{item.category}</Tag>
                    {item.score ? <Tag color="green">{Math.round(item.score * 100)}%</Tag> : null}
                  </Space>
                }
                description={
                  <Space direction="vertical" size={4}>
                    <Typography.Text>{item.excerpt}</Typography.Text>
                    <Typography.Text type="secondary">{item.source}</Typography.Text>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
}

export default KnowledgePage;
