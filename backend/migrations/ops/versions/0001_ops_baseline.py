"""The schema of Liner's own database as `create_all` built it before migrations.

Generated from the models, not written by hand, and never edited since: it is
what `app.migrate.ensure` stamps a database built before migrations as being
at, so it has to describe exactly that. Every change after it is a revision of
its own.

Revision ID: 0001_ops_baseline
Revises: (none)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0001_ops_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('ops_demo_requests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('dealership', sa.String(length=160), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=False),
    sa.Column('dealership_url', sa.String(length=500), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('slot_at', sa.DateTime(), nullable=True),
    sa.Column('consent_at', sa.DateTime(), nullable=True),
    sa.Column('consent_text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ops_demo_requests'))
    )
    with op.batch_alter_table('ops_demo_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ops_demo_requests_email'), ['email'], unique=False)
        batch_op.create_index(batch_op.f('ix_ops_demo_requests_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_ops_demo_requests_slot_at'), ['slot_at'], unique=False)

    op.create_table('ops_mail_state',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('ref_id', sa.String(length=36), nullable=False),
    sa.Column('read_at', sa.DateTime(), nullable=True),
    sa.Column('trashed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ops_mail_state')),
    sa.UniqueConstraint('kind', 'ref_id', name='uq_ops_mail_state_ref')
    )
    with op.batch_alter_table('ops_mail_state', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ops_mail_state_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_ops_mail_state_ref_id'), ['ref_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ops_mail_state_trashed_at'), ['trashed_at'], unique=False)

    op.create_table('ops_messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('author_id', sa.String(length=36), nullable=False),
    sa.Column('to_address', sa.String(length=255), nullable=False),
    sa.Column('subject', sa.String(length=500), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('from_address', sa.String(length=320), nullable=False),
    sa.Column('reply_to', sa.String(length=255), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('provider_message_id', sa.String(length=255), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('reply_to_kind', sa.String(length=12), nullable=False),
    sa.Column('reply_to_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('trashed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ops_messages'))
    )
    with op.batch_alter_table('ops_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ops_messages_author_id'), ['author_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ops_messages_sent_at'), ['sent_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_ops_messages_state'), ['state'], unique=False)
        batch_op.create_index(batch_op.f('ix_ops_messages_trashed_at'), ['trashed_at'], unique=False)

    op.create_table('ops_sms_opt_outs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('phone_key', sa.String(length=10), nullable=False),
    sa.Column('phone', sa.String(length=32), nullable=False),
    sa.Column('reason', sa.String(length=40), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('resumed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ops_sms_opt_outs'))
    )
    with op.batch_alter_table('ops_sms_opt_outs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ops_sms_opt_outs_phone_key'), ['phone_key'], unique=True)

    op.create_table('ops_users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('password_env', sa.String(length=40), nullable=False),
    sa.Column('avatar_initials', sa.String(length=4), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ops_users'))
    )
    with op.batch_alter_table('ops_users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ops_users_email'), ['email'], unique=True)

    op.create_table('ops_mail_attachments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('message_id', sa.String(length=36), nullable=True),
    sa.Column('uploaded_by', sa.String(length=36), nullable=True),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('path', sa.String(length=500), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['ops_messages.id'], name=op.f('fk_ops_mail_attachments_message_id_ops_messages')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ops_mail_attachments'))
    )
    with op.batch_alter_table('ops_mail_attachments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ops_mail_attachments_message_id'), ['message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ops_mail_attachments_uploaded_by'), ['uploaded_by'], unique=False)

    op.create_table('ops_mail_envelopes',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('message_id', sa.String(length=36), nullable=False),
    sa.Column('to_json', sa.Text(), nullable=False),
    sa.Column('cc_json', sa.Text(), nullable=False),
    sa.Column('bcc_json', sa.Text(), nullable=False),
    sa.Column('html', sa.Text(), nullable=False),
    sa.Column('rfc_message_id', sa.String(length=255), nullable=False),
    sa.Column('in_reply_to', sa.String(length=255), nullable=False),
    sa.Column('references', sa.Text(), nullable=False),
    sa.Column('importance', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['ops_messages.id'], name=op.f('fk_ops_mail_envelopes_message_id_ops_messages')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ops_mail_envelopes'))
    )
    with op.batch_alter_table('ops_mail_envelopes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ops_mail_envelopes_message_id'), ['message_id'], unique=True)

    op.create_table('ops_phone_calls',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('direction', sa.String(length=4), nullable=False),
    sa.Column('call_sid', sa.String(length=64), nullable=False),
    sa.Column('from_number', sa.String(length=32), nullable=False),
    sa.Column('to_number', sa.String(length=32), nullable=False),
    sa.Column('persona', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('duration_sec', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('placed_by_user_id', sa.String(length=36), nullable=True),
    sa.Column('demo_request_id', sa.String(length=36), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('ended_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['demo_request_id'], ['ops_demo_requests.id'], name=op.f('fk_ops_phone_calls_demo_request_id_ops_demo_requests')),
    sa.ForeignKeyConstraint(['placed_by_user_id'], ['ops_users.id'], name=op.f('fk_ops_phone_calls_placed_by_user_id_ops_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ops_phone_calls'))
    )
    with op.batch_alter_table('ops_phone_calls', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ops_phone_calls_call_sid'), ['call_sid'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('ops_phone_calls', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ops_phone_calls_call_sid'))

    op.drop_table('ops_phone_calls')
    with op.batch_alter_table('ops_mail_envelopes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ops_mail_envelopes_message_id'))

    op.drop_table('ops_mail_envelopes')
    with op.batch_alter_table('ops_mail_attachments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ops_mail_attachments_uploaded_by'))
        batch_op.drop_index(batch_op.f('ix_ops_mail_attachments_message_id'))

    op.drop_table('ops_mail_attachments')
    with op.batch_alter_table('ops_users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ops_users_email'))

    op.drop_table('ops_users')
    with op.batch_alter_table('ops_sms_opt_outs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ops_sms_opt_outs_phone_key'))

    op.drop_table('ops_sms_opt_outs')
    with op.batch_alter_table('ops_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ops_messages_trashed_at'))
        batch_op.drop_index(batch_op.f('ix_ops_messages_state'))
        batch_op.drop_index(batch_op.f('ix_ops_messages_sent_at'))
        batch_op.drop_index(batch_op.f('ix_ops_messages_author_id'))

    op.drop_table('ops_messages')
    with op.batch_alter_table('ops_mail_state', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ops_mail_state_trashed_at'))
        batch_op.drop_index(batch_op.f('ix_ops_mail_state_ref_id'))
        batch_op.drop_index(batch_op.f('ix_ops_mail_state_kind'))

    op.drop_table('ops_mail_state')
    with op.batch_alter_table('ops_demo_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ops_demo_requests_slot_at'))
        batch_op.drop_index(batch_op.f('ix_ops_demo_requests_kind'))
        batch_op.drop_index(batch_op.f('ix_ops_demo_requests_email'))

    op.drop_table('ops_demo_requests')
