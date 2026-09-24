"""The schema of a dealer group's database as `create_all` built it before migrations.

Generated from the models, not written by hand, and never edited since: it is
what `app.migrate.ensure` stamps a database built before migrations as being
at, so it has to describe exactly that. Every change after it is a revision of
its own.

Revision ID: 0001_store_baseline
Revises: (none)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0001_store_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('assistant_settings',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('tone', sa.String(length=30), nullable=False),
    sa.Column('push_level', sa.String(length=30), nullable=False),
    sa.Column('price_mode', sa.String(length=30), nullable=False),
    sa.Column('discount_pct', sa.Integer(), nullable=False),
    sa.Column('financing_mode', sa.String(length=30), nullable=False),
    sa.Column('after_hours_mode', sa.String(length=30), nullable=False),
    sa.Column('greeting', sa.Text(), nullable=False),
    sa.Column('booking_slot_length', sa.Integer(), nullable=False),
    sa.Column('credit_application_url', sa.String(length=500), nullable=False),
    sa.Column('published_by', sa.String(length=36), nullable=True),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_assistant_settings'))
    )
    op.create_table('dealership',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('hours_json', sa.Text(), nullable=False),
    sa.Column('address', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=False),
    sa.Column('website_url', sa.String(length=255), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dealership'))
    )
    op.create_table('events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('type', sa.String(length=60), nullable=False),
    sa.Column('payload_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_events'))
    )
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_events_type'), ['type'], unique=False)

    op.create_table('handoff_rules',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('label', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('threshold_value', sa.Integer(), nullable=True),
    sa.Column('threshold_unit', sa.String(length=30), nullable=False),
    sa.Column('route_target', sa.String(length=60), nullable=False),
    sa.Column('notify', sa.String(length=40), nullable=False),
    sa.Column('fired_count', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_handoff_rules')),
    sa.UniqueConstraint('key', name=op.f('uq_handoff_rules_key'))
    )
    op.create_table('ingest_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('source_url', sa.String(length=255), nullable=False),
    sa.Column('method', sa.String(length=30), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('listings_found', sa.Integer(), nullable=False),
    sa.Column('created_count', sa.Integer(), nullable=False),
    sa.Column('updated_count', sa.Integer(), nullable=False),
    sa.Column('removed_count', sa.Integer(), nullable=False),
    sa.Column('diff_json', sa.Text(), nullable=False),
    sa.Column('errors_json', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ingest_runs'))
    )
    op.create_table('knowledge_entries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('topic', sa.String(length=120), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('use_count', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_entries'))
    )
    op.create_table('link_clicks',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_link_clicks'))
    )
    with op.batch_alter_table('link_clicks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_link_clicks_kind'), ['kind'], unique=False)

    op.create_table('rails',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('stage', sa.String(length=30), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('message_text', sa.Text(), nullable=False),
    sa.Column('requires_vehicle', sa.Boolean(), nullable=False),
    sa.Column('knowledge_entry_id', sa.String(length=36), nullable=True),
    sa.Column('advances_to', sa.String(length=30), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('action_json', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rails'))
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('avatar_initials', sa.String(length=4), nullable=False),
    sa.Column('daily_cap', sa.Integer(), nullable=False),
    sa.Column('notify_channel', sa.String(length=20), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('vehicles',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('vin', sa.String(length=17), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('make', sa.String(length=60), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('trim', sa.String(length=60), nullable=False),
    sa.Column('price', sa.Integer(), nullable=True),
    sa.Column('mileage', sa.Integer(), nullable=True),
    sa.Column('body_style', sa.String(length=40), nullable=False),
    sa.Column('seats', sa.Integer(), nullable=True),
    sa.Column('title_status', sa.String(length=40), nullable=False),
    sa.Column('features_json', sa.Text(), nullable=False),
    sa.Column('keywords', sa.Text(), nullable=False),
    sa.Column('photo_url', sa.String(length=255), nullable=False),
    sa.Column('listing_url', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('rule_discuss', sa.Boolean(), nullable=False),
    sa.Column('rule_hold_price', sa.Boolean(), nullable=False),
    sa.Column('rule_mention_warranty', sa.Boolean(), nullable=False),
    sa.Column('rule_note', sa.Text(), nullable=False),
    sa.Column('manual_fields_json', sa.Text(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('ingest_run_id', sa.String(length=36), nullable=True),
    sa.Column('raw_json', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_vehicles'))
    )
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vehicles_vin'), ['vin'], unique=True)

    op.create_table('widget_installs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('origin', sa.String(length=255), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('last_page', sa.String(length=1000), nullable=False),
    sa.Column('loader_version', sa.String(length=20), nullable=False),
    sa.Column('other_widgets_json', sa.Text(), nullable=False),
    sa.Column('duplicate_tag', sa.Boolean(), nullable=False),
    sa.Column('gtm_present', sa.Boolean(), nullable=False),
    sa.Column('reports', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_widget_installs'))
    )
    with op.batch_alter_table('widget_installs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_widget_installs_origin'), ['origin'], unique=True)

    op.create_table('assistant_parts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('settings_id', sa.String(length=36), nullable=False),
    sa.Column('part', sa.String(length=20), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.String(length=36), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['settings_id'], ['assistant_settings.id'], name=op.f('fk_assistant_parts_settings_id_assistant_settings')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_assistant_parts'))
    )
    with op.batch_alter_table('assistant_parts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assistant_parts_settings_id'), ['settings_id'], unique=False)

    op.create_table('assistant_prompts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('settings_id', sa.String(length=36), nullable=False),
    sa.Column('brief', sa.Text(), nullable=False),
    sa.Column('rules', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.String(length=36), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['settings_id'], ['assistant_settings.id'], name=op.f('fk_assistant_prompts_settings_id_assistant_settings')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_assistant_prompts'))
    )
    with op.batch_alter_table('assistant_prompts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assistant_prompts_settings_id'), ['settings_id'], unique=True)

    op.create_table('leads',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('assigned_user_id', sa.String(length=36), nullable=True),
    sa.Column('email_consent_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'], name=op.f('fk_leads_assigned_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads'))
    )
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_leads_email'), ['email'], unique=False)

    op.create_table('runtime_flags',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('value', sa.String(length=200), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('set_by_user_id', sa.String(length=36), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['set_by_user_id'], ['users.id'], name=op.f('fk_runtime_flags_set_by_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_runtime_flags'))
    )
    with op.batch_alter_table('runtime_flags', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_runtime_flags_key'), ['key'], unique=True)

    op.create_table('user_signatures',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('image_token', sa.String(length=40), nullable=False),
    sa.Column('image_ext', sa.String(length=8), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_signatures_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_signatures'))
    )
    with op.batch_alter_table('user_signatures', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_signatures_image_token'), ['image_token'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_signatures_user_id'), ['user_id'], unique=True)

    op.create_table('appointments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('lead_id', sa.String(length=36), nullable=False),
    sa.Column('vehicle_id', sa.String(length=36), nullable=True),
    sa.Column('assigned_user_id', sa.String(length=36), nullable=True),
    sa.Column('starts_at', sa.DateTime(), nullable=False),
    sa.Column('duration_min', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('booked_by', sa.String(length=10), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=True),
    sa.Column('tool_call_id', sa.String(length=80), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'], name=op.f('fk_appointments_assigned_user_id_users')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_appointments_lead_id_leads')),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_appointments_vehicle_id_vehicles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_appointments'))
    )
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_appointments_lead_id'), ['lead_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_appointments_starts_at'), ['starts_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_appointments_tool_call_id'), ['tool_call_id'], unique=False)

    op.create_table('captured_fields',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('lead_id', sa.String(length=36), nullable=False),
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('provenance', sa.String(length=20), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_captured_fields_lead_id_leads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_captured_fields')),
    sa.UniqueConstraint('lead_id', 'key', name='uq_captured_lead_key')
    )
    with op.batch_alter_table('captured_fields', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_captured_fields_lead_id'), ['lead_id'], unique=False)

    op.create_table('conversations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('lead_id', sa.String(length=36), nullable=True),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('agent_paused', sa.Boolean(), nullable=False),
    sa.Column('stage', sa.String(length=30), nullable=False),
    sa.Column('focus_vehicle_id', sa.String(length=36), nullable=True),
    sa.Column('last_results_json', sa.Text(), nullable=False),
    sa.Column('offered_slots_json', sa.Text(), nullable=False),
    sa.Column('chosen_slot', sa.String(length=40), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('ended_at', sa.DateTime(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_conversations_lead_id_leads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_conversations_lead_id'), ['lead_id'], unique=False)

    op.create_table('lead_addresses',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('lead_id', sa.String(length=36), nullable=False),
    sa.Column('address', sa.String(length=255), nullable=False),
    sa.Column('added_by_user_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['added_by_user_id'], ['users.id'], name=op.f('fk_lead_addresses_added_by_user_id_users')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_lead_addresses_lead_id_leads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lead_addresses'))
    )
    with op.batch_alter_table('lead_addresses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_lead_addresses_address'), ['address'], unique=False)
        batch_op.create_index(batch_op.f('ix_lead_addresses_lead_id'), ['lead_id'], unique=False)

    op.create_table('call_buyer_tracks',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('filename', sa.String(length=120), nullable=False),
    sa.Column('content_type', sa.String(length=60), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('transcribed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_call_buyer_tracks_conversation_id_conversations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_call_buyer_tracks'))
    )
    with op.batch_alter_table('call_buyer_tracks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_call_buyer_tracks_conversation_id'), ['conversation_id'], unique=True)

    op.create_table('call_recordings',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('filename', sa.String(length=120), nullable=False),
    sa.Column('content_type', sa.String(length=60), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_call_recordings_conversation_id_conversations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_call_recordings'))
    )
    with op.batch_alter_table('call_recordings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_call_recordings_conversation_id'), ['conversation_id'], unique=True)

    op.create_table('call_segments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('speaker', sa.String(length=12), nullable=False),
    sa.Column('started_ms', sa.Integer(), nullable=False),
    sa.Column('ended_ms', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_call_segments_conversation_id_conversations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_call_segments'))
    )
    with op.batch_alter_table('call_segments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_call_segments_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_call_segments_started_ms'), ['started_ms'], unique=False)

    op.create_table('call_usage',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('response_id', sa.String(length=80), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('input_audio_tokens', sa.Integer(), nullable=False),
    sa.Column('input_text_tokens', sa.Integer(), nullable=False),
    sa.Column('cached_tokens', sa.Integer(), nullable=False),
    sa.Column('cached_audio_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('output_audio_tokens', sa.Integer(), nullable=False),
    sa.Column('output_text_tokens', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_call_usage_conversation_id_conversations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_call_usage'))
    )
    with op.batch_alter_table('call_usage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_call_usage_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_call_usage_response_id'), ['response_id'], unique=False)

    op.create_table('conversation_once',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_conversation_once_conversation_id_conversations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversation_once')),
    sa.UniqueConstraint('conversation_id', 'key', name='uq_convo_once')
    )
    with op.batch_alter_table('conversation_once', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_conversation_once_conversation_id'), ['conversation_id'], unique=False)

    op.create_table('conversation_pages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('url', sa.String(length=1000), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('vin', sa.String(length=17), nullable=False),
    sa.Column('vehicle_id', sa.String(length=36), nullable=True),
    sa.Column('seen_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_conversation_pages_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_conversation_pages_vehicle_id_vehicles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversation_pages'))
    )
    with op.batch_alter_table('conversation_pages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_conversation_pages_conversation_id'), ['conversation_id'], unique=False)

    op.create_table('escalations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('handoff_rule_id', sa.String(length=36), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('claimed_by_user_id', sa.String(length=36), nullable=True),
    sa.Column('claimed_at', sa.DateTime(), nullable=True),
    sa.Column('tool_call_id', sa.String(length=80), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['claimed_by_user_id'], ['users.id'], name=op.f('fk_escalations_claimed_by_user_id_users')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_escalations_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['handoff_rule_id'], ['handoff_rules.id'], name=op.f('fk_escalations_handoff_rule_id_handoff_rules')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_escalations'))
    )
    with op.batch_alter_table('escalations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_escalations_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_escalations_tool_call_id'), ['tool_call_id'], unique=False)

    op.create_table('messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('tool_calls_json', sa.Text(), nullable=False),
    sa.Column('via_rail_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_messages_conversation_id_conversations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages'))
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_messages_conversation_id'), ['conversation_id'], unique=False)

    op.create_table('outreach',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('appointment_id', sa.String(length=36), nullable=True),
    sa.Column('lead_id', sa.String(length=36), nullable=True),
    sa.Column('sent_by_user_id', sa.String(length=36), nullable=True),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('direction', sa.String(length=3), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('to_address', sa.String(length=255), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('provider_message_id', sa.String(length=120), nullable=True),
    sa.Column('provider_thread_id', sa.String(length=120), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('reply_token', sa.String(length=64), nullable=True),
    sa.Column('in_reply_to', sa.String(length=200), nullable=True),
    sa.Column('click_token', sa.String(length=64), nullable=True),
    sa.Column('click_count', sa.Integer(), nullable=False),
    sa.Column('first_clicked_at', sa.DateTime(), nullable=True),
    sa.Column('last_clicked_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['appointment_id'], ['appointments.id'], name=op.f('fk_outreach_appointment_id_appointments')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_outreach_lead_id_leads')),
    sa.ForeignKeyConstraint(['sent_by_user_id'], ['users.id'], name=op.f('fk_outreach_sent_by_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outreach'))
    )
    with op.batch_alter_table('outreach', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_outreach_appointment_id'), ['appointment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_outreach_click_token'), ['click_token'], unique=False)
        batch_op.create_index(batch_op.f('ix_outreach_direction'), ['direction'], unique=False)
        batch_op.create_index(batch_op.f('ix_outreach_in_reply_to'), ['in_reply_to'], unique=False)
        batch_op.create_index(batch_op.f('ix_outreach_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_outreach_reply_token'), ['reply_token'], unique=False)

    op.create_table('vehicle_mentions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('message_id', sa.String(length=36), nullable=True),
    sa.Column('vehicle_id', sa.String(length=36), nullable=False),
    sa.Column('quoted_price', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_vehicle_mentions_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_vehicle_mentions_vehicle_id_vehicles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_vehicle_mentions'))
    )
    with op.batch_alter_table('vehicle_mentions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vehicle_mentions_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_vehicle_mentions_vehicle_id'), ['vehicle_id'], unique=False)

    op.create_table('inbound_emails',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('message_id', sa.String(length=200), nullable=False),
    sa.Column('from_address', sa.String(length=255), nullable=False),
    sa.Column('to_address', sa.String(length=255), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('in_reply_to', sa.String(length=200), nullable=False),
    sa.Column('matched_by', sa.String(length=20), nullable=False),
    sa.Column('lead_id', sa.String(length=36), nullable=True),
    sa.Column('outreach_id', sa.String(length=36), nullable=True),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_inbound_emails_lead_id_leads')),
    sa.ForeignKeyConstraint(['outreach_id'], ['outreach.id'], name=op.f('fk_inbound_emails_outreach_id_outreach')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inbound_emails'))
    )
    with op.batch_alter_table('inbound_emails', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_inbound_emails_message_id'), ['message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_inbound_emails_outcome'), ['outcome'], unique=False)

    op.create_table('email_envelopes',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('outreach_id', sa.String(length=36), nullable=True),
    sa.Column('receipt_id', sa.String(length=36), nullable=True),
    sa.Column('rfc_message_id', sa.String(length=255), nullable=False),
    sa.Column('in_reply_to', sa.String(length=255), nullable=False),
    sa.Column('references', sa.Text(), nullable=False),
    sa.Column('from_name', sa.String(length=255), nullable=False),
    sa.Column('from_address', sa.String(length=320), nullable=False),
    sa.Column('to_json', sa.Text(), nullable=False),
    sa.Column('cc_json', sa.Text(), nullable=False),
    sa.Column('bcc_json', sa.Text(), nullable=False),
    sa.Column('reply_to_json', sa.Text(), nullable=False),
    sa.Column('html', sa.Text(), nullable=False),
    sa.Column('importance', sa.String(length=10), nullable=False),
    sa.Column('dated_at', sa.DateTime(), nullable=True),
    sa.Column('raw_path', sa.String(length=500), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['outreach_id'], ['outreach.id'], name=op.f('fk_email_envelopes_outreach_id_outreach')),
    sa.ForeignKeyConstraint(['receipt_id'], ['inbound_emails.id'], name=op.f('fk_email_envelopes_receipt_id_inbound_emails')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_envelopes'))
    )
    with op.batch_alter_table('email_envelopes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_envelopes_outreach_id'), ['outreach_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_email_envelopes_receipt_id'), ['receipt_id'], unique=True)

    op.create_table('email_replies_due',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('inbound_email_id', sa.String(length=36), nullable=False),
    sa.Column('lead_id', sa.String(length=36), nullable=False),
    sa.Column('outreach_id', sa.String(length=36), nullable=True),
    sa.Column('due_at', sa.DateTime(), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('automated', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['inbound_email_id'], ['inbound_emails.id'], name=op.f('fk_email_replies_due_inbound_email_id_inbound_emails')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_email_replies_due_lead_id_leads')),
    sa.ForeignKeyConstraint(['outreach_id'], ['outreach.id'], name=op.f('fk_email_replies_due_outreach_id_outreach')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_replies_due'))
    )
    with op.batch_alter_table('email_replies_due', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_replies_due_due_at'), ['due_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_replies_due_inbound_email_id'), ['inbound_email_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_replies_due_lead_id'), ['lead_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_replies_due_state'), ['state'], unique=False)

    op.create_table('email_attachments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('envelope_id', sa.String(length=36), nullable=True),
    sa.Column('uploaded_by', sa.String(length=36), nullable=True),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('path', sa.String(length=500), nullable=False),
    sa.Column('content_id', sa.String(length=255), nullable=False),
    sa.Column('disposition', sa.String(length=12), nullable=False),
    sa.Column('refused', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['envelope_id'], ['email_envelopes.id'], name=op.f('fk_email_attachments_envelope_id_email_envelopes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_attachments'))
    )
    with op.batch_alter_table('email_attachments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_attachments_envelope_id'), ['envelope_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_attachments_uploaded_by'), ['uploaded_by'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('email_attachments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_attachments_uploaded_by'))
        batch_op.drop_index(batch_op.f('ix_email_attachments_envelope_id'))

    op.drop_table('email_attachments')
    with op.batch_alter_table('email_replies_due', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_replies_due_state'))
        batch_op.drop_index(batch_op.f('ix_email_replies_due_lead_id'))
        batch_op.drop_index(batch_op.f('ix_email_replies_due_inbound_email_id'))
        batch_op.drop_index(batch_op.f('ix_email_replies_due_due_at'))

    op.drop_table('email_replies_due')
    with op.batch_alter_table('email_envelopes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_envelopes_receipt_id'))
        batch_op.drop_index(batch_op.f('ix_email_envelopes_outreach_id'))

    op.drop_table('email_envelopes')
    with op.batch_alter_table('inbound_emails', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inbound_emails_outcome'))
        batch_op.drop_index(batch_op.f('ix_inbound_emails_message_id'))

    op.drop_table('inbound_emails')
    with op.batch_alter_table('vehicle_mentions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vehicle_mentions_vehicle_id'))
        batch_op.drop_index(batch_op.f('ix_vehicle_mentions_conversation_id'))

    op.drop_table('vehicle_mentions')
    with op.batch_alter_table('outreach', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_outreach_reply_token'))
        batch_op.drop_index(batch_op.f('ix_outreach_kind'))
        batch_op.drop_index(batch_op.f('ix_outreach_in_reply_to'))
        batch_op.drop_index(batch_op.f('ix_outreach_direction'))
        batch_op.drop_index(batch_op.f('ix_outreach_click_token'))
        batch_op.drop_index(batch_op.f('ix_outreach_appointment_id'))

    op.drop_table('outreach')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_conversation_id'))

    op.drop_table('messages')
    with op.batch_alter_table('escalations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_escalations_tool_call_id'))
        batch_op.drop_index(batch_op.f('ix_escalations_conversation_id'))

    op.drop_table('escalations')
    with op.batch_alter_table('conversation_pages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_conversation_pages_conversation_id'))

    op.drop_table('conversation_pages')
    with op.batch_alter_table('conversation_once', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_conversation_once_conversation_id'))

    op.drop_table('conversation_once')
    with op.batch_alter_table('call_usage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_call_usage_response_id'))
        batch_op.drop_index(batch_op.f('ix_call_usage_conversation_id'))

    op.drop_table('call_usage')
    with op.batch_alter_table('call_segments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_call_segments_started_ms'))
        batch_op.drop_index(batch_op.f('ix_call_segments_conversation_id'))

    op.drop_table('call_segments')
    with op.batch_alter_table('call_recordings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_call_recordings_conversation_id'))

    op.drop_table('call_recordings')
    with op.batch_alter_table('call_buyer_tracks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_call_buyer_tracks_conversation_id'))

    op.drop_table('call_buyer_tracks')
    with op.batch_alter_table('lead_addresses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_lead_addresses_lead_id'))
        batch_op.drop_index(batch_op.f('ix_lead_addresses_address'))

    op.drop_table('lead_addresses')
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_conversations_lead_id'))

    op.drop_table('conversations')
    with op.batch_alter_table('captured_fields', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_captured_fields_lead_id'))

    op.drop_table('captured_fields')
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_appointments_tool_call_id'))
        batch_op.drop_index(batch_op.f('ix_appointments_starts_at'))
        batch_op.drop_index(batch_op.f('ix_appointments_lead_id'))

    op.drop_table('appointments')
    with op.batch_alter_table('user_signatures', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_signatures_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_signatures_image_token'))

    op.drop_table('user_signatures')
    with op.batch_alter_table('runtime_flags', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_runtime_flags_key'))

    op.drop_table('runtime_flags')
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_leads_email'))

    op.drop_table('leads')
    with op.batch_alter_table('assistant_prompts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assistant_prompts_settings_id'))

    op.drop_table('assistant_prompts')
    with op.batch_alter_table('assistant_parts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assistant_parts_settings_id'))

    op.drop_table('assistant_parts')
    with op.batch_alter_table('widget_installs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_widget_installs_origin'))

    op.drop_table('widget_installs')
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vehicles_vin'))

    op.drop_table('vehicles')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    op.drop_table('rails')
    with op.batch_alter_table('link_clicks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_link_clicks_kind'))

    op.drop_table('link_clicks')
    op.drop_table('knowledge_entries')
    op.drop_table('ingest_runs')
    op.drop_table('handoff_rules')
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_events_type'))

    op.drop_table('events')
    op.drop_table('dealership')
    op.drop_table('assistant_settings')
