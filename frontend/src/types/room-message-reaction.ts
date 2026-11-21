export interface RoomMessageReactionAgent {
  agent_id: string;
  name: string;
}

export interface RoomMessageReactionSummary {
  emoji: string;
  count: number;
  reacted_by_me: boolean;
  agents?: RoomMessageReactionAgent[];
}

export interface RoomMessageReactionsRead {
  reactions: RoomMessageReactionSummary[];
}
