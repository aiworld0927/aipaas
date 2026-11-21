package com.aipaas.platform.conversation;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/** 会话与消息持久化：全部按 tenant_id + user_id 隔离。 */
@Repository
public class ConversationRepository {

    private final JdbcTemplate jdbc;

    public ConversationRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private final RowMapper<ConversationRow> mapper = (rs, n) -> new ConversationRow(
            rs.getString("id"), rs.getString("tenant_id"), rs.getString("user_id"), rs.getString("title"),
            rs.getObject("created_at", OffsetDateTime.class), rs.getObject("updated_at", OffsetDateTime.class));

    private final RowMapper<MessageRow> messageMapper = (rs, n) -> new MessageRow(
            rs.getString("id"), rs.getString("conversation_id"), rs.getString("role"), rs.getString("content"),
            rs.getString("citations_json"), rs.getObject("created_at", OffsetDateTime.class));

    public List<ConversationRow> list(String tenantId, String userId, String keyword) {
        StringBuilder sql = new StringBuilder("select * from conversations where tenant_id=? and user_id=? "
                + "and deleted_at is null");
        List<Object> args = new ArrayList<>(List.of(tenantId, userId));
        if (keyword != null && !keyword.isBlank()) {
            sql.append(" and lower(title) like ?");
            args.add("%" + keyword.trim().toLowerCase() + "%");
        }
        sql.append(" order by updated_at desc limit 200");
        return jdbc.query(sql.toString(), mapper, args.toArray());
    }

    public Optional<ConversationRow> find(String tenantId, String userId, String id) {
        return jdbc.query("select * from conversations where tenant_id=? and user_id=? and id=? and deleted_at is null",
                mapper, tenantId, userId, id).stream().findFirst();
    }

    public void create(String id, String tenantId, String userId, String title, OffsetDateTime now) {
        jdbc.update("insert into conversations(id,tenant_id,user_id,title,created_at,updated_at) values(?,?,?,?,?,?)",
                id, tenantId, userId, title, now, now);
    }

    public int rename(String tenantId, String userId, String id, String title, OffsetDateTime now) {
        return jdbc.update("update conversations set title=?, updated_at=? "
                + "where tenant_id=? and user_id=? and id=? and deleted_at is null", title, now, tenantId, userId, id);
    }

    public int touch(String tenantId, String userId, String id, OffsetDateTime now) {
        return jdbc.update("update conversations set updated_at=? where tenant_id=? and user_id=? and id=? "
                + "and deleted_at is null", now, tenantId, userId, id);
    }

    public int softDelete(String tenantId, String userId, String id, OffsetDateTime now) {
        return jdbc.update("update conversations set deleted_at=? where tenant_id=? and user_id=? and id=? "
                + "and deleted_at is null", now, tenantId, userId, id);
    }

    public List<MessageRow> messages(String tenantId, String userId, String conversationId) {
        return jdbc.query("select m.* from conversation_messages m join conversations c on c.id=m.conversation_id "
                        + "where c.tenant_id=? and c.user_id=? and c.deleted_at is null and m.conversation_id=? "
                        + "order by m.created_at, m.id",
                messageMapper, tenantId, userId, conversationId);
    }

    public void addMessage(String id, String tenantId, String conversationId, String userId, String role,
                           String content, String citationsJson, OffsetDateTime now) {
        jdbc.update("insert into conversation_messages(id,tenant_id,conversation_id,user_id,role,content,"
                        + "citations_json,created_at) values(?,?,?,?,?,?,?,?)",
                id, tenantId, conversationId, userId, role, content, citationsJson, now);
    }

    public int clearMessages(String tenantId, String conversationId) {
        return jdbc.update("delete from conversation_messages where tenant_id=? and conversation_id=?",
                tenantId, conversationId);
    }

    public record ConversationRow(String id, String tenantId, String userId, String title,
                                  OffsetDateTime createdAt, OffsetDateTime updatedAt) {
    }

    public record MessageRow(String id, String conversationId, String role, String content, String citationsJson,
                             OffsetDateTime createdAt) {
    }
}
