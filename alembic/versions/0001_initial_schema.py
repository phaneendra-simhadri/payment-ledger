"""create payment ledger schema

Revision ID: 0001_initial_schema
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    user_id = postgresql.UUID(as_uuid=True)
    is_postgresql = op.get_bind().dialect.name == 'postgresql'
    json_type = postgresql.JSONB if is_postgresql else sa.JSON
    if is_postgresql:
        op.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')
    op.create_table('users',
        sa.Column('id', user_id, server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('email'),
    )
    op.create_table('accounts',
        sa.Column('id', user_id, server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('owner_id', user_id, sa.ForeignKey('users.id', ondelete='RESTRICT')),
        sa.Column('account_type', sa.String(20), nullable=False),
        sa.Column('currency', sa.String(3), server_default='INR', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("account_type IN ('user_wallet', 'system_bank', 'system_revenue')", name='account_type_check'),
    )
    op.create_table('transactions',
        sa.Column('id', user_id, server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('idempotency_key', sa.String(64), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('type', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), server_default='pending', nullable=False),
        sa.Column('amount', sa.Numeric(18, 2), nullable=False),
        sa.Column('currency', sa.String(3), server_default='INR', nullable=False),
        sa.Column('from_account_id', user_id, sa.ForeignKey('accounts.id')),
        sa.Column('to_account_id', user_id, sa.ForeignKey('accounts.id')),
        sa.Column('metadata', json_type),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('idempotency_key'),
        sa.CheckConstraint('amount > 0', name='transaction_amount_positive'),
        sa.CheckConstraint("type IN ('deposit', 'withdrawal', 'transfer')", name='type_check'),
        sa.CheckConstraint("status IN ('pending', 'completed', 'failed')", name='status_check'),
    )
    op.create_table('ledger_entries',
        sa.Column('id', user_id, server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('transaction_id', user_id, sa.ForeignKey('transactions.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('account_id', user_id, sa.ForeignKey('accounts.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('entry_type', sa.String(6), nullable=False),
        sa.Column('amount', sa.Numeric(18, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("entry_type IN ('debit', 'credit')", name='entry_type_check'),
        sa.CheckConstraint('amount > 0', name='ledger_amount_positive'),
    )
    op.create_table('webhook_deliveries',
        sa.Column('id', user_id, server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('transaction_id', user_id, sa.ForeignKey('transactions.id'), nullable=False),
        sa.Column('target_url', sa.Text(), nullable=False),
        sa.Column('payload', json_type, nullable=False),
        sa.Column('status', sa.String(20), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_attempted_at', sa.DateTime(timezone=True)),
        sa.Column('next_retry_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table('reconciliation_logs',
        sa.Column('id', user_id, server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('run_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('accounts_checked', sa.Integer(), nullable=False),
        sa.Column('mismatches_found', sa.Integer(), server_default='0', nullable=False),
        sa.Column('mismatch_details', json_type),
        sa.Column('status', sa.String(20), nullable=False),
    )
    op.create_index('idx_transactions_idempotency', 'transactions', ['idempotency_key'])
    op.create_index('idx_transactions_from_account', 'transactions', ['from_account_id'])
    op.create_index('idx_transactions_to_account', 'transactions', ['to_account_id'])
    op.create_index('idx_transactions_created_at', 'transactions', ['created_at'])
    op.create_index('idx_ledger_account', 'ledger_entries', ['account_id'])
    op.create_index('idx_ledger_transaction', 'ledger_entries', ['transaction_id'])


def downgrade():
    op.drop_table('reconciliation_logs')
    op.drop_table('webhook_deliveries')
    op.drop_table('ledger_entries')
    op.drop_table('transactions')
    op.drop_table('accounts')
    op.drop_table('users')
