#!/usr/bin/env python3
'''
Author: liguoqiang
Date: 2025-03-15 09:47:54
LastEditors: liguoqiang
LastEditTime: 2025-09-18 20:28:52
Description: 
'''
from nicegui import ui

from app_context import AppContext


def show_stocks_table(datas, show_edit) -> ui.table:
    current_theme = AppContext().theme_manager.get_current_theme()
    bg_color = current_theme.get('background')
    font_color = current_theme.get('font_color', '#c9d1d9')
    widget_border_color = current_theme.get('widget_border_color', '#80808033')
    table_columns = [
        {'name': 'id', 'label': 'id', 'field': 'id', 'width': '0%', 'align': 'center'},
        {'name': 'sn', 'label': '序号', 'field': 'sn', 'width': '2%', 'align': 'center'},
        {'name': 'code', 'label': '代码', 'field': 'code', 'width': '5%', 'align': 'center'},
        {'name': 'name', 'label': '名称', 'field': 'name', 'width': '5%', 'align': 'center'},
        {'name': 'price', 'label': '价格', 'field': 'price', 'width': '5%', 'align': 'center'},
        {'name': 'change_percent', 'label': '涨跌幅', 'field': 'change_percent', 'width': '5%', 'align': 'center'},
        {'name': 'change_amount', 'label': '涨跌额', 'field': 'change_amount', 'width': '5%', 'align': 'center'},
        {'name': 'open', 'label': '开盘价', 'field': 'open', 'width': '5%', 'align': 'center'},
        {'name': 'high', 'label': '最高价', 'field': 'high', 'width': '5%', 'align': 'center'},
        {'name': 'low', 'label': '最低价', 'field': 'low', 'width': '5%', 'align': 'center'},
        {'name': 'volume', 'label': '成交量', 'field': 'volume', 'width': '5%', 'align': 'center'},
        {'name': 'amount', 'label': '成交额', 'field': 'amount', 'width': '5%', 'align': 'center'}
    ]
    with ui.table(
        columns=table_columns,
        rows=datas,
        row_key='id',
        selection='single',
        pagination={'rowsPerPage': 0, 'sortBy': 'sn', 'page': 1}) \
            .props(f'table-header-style="color: {font_color}; font-size: 16px; background-color: {bg_color}"') \
            .classes('w-full mt-2 gap-0') \
            .style(f'border: 1px solid {widget_border_color}; border-radius: 10px 10px 0px 0px;') as table:
        table.props('v-model:selected="selected"')
        table.props('visible-columns="[\'sn\', \
                    \'code\', \
                    \'name\', \
                    \'price\', \
                    \'change_percent\', \
                    \'change_amount\', \
                    \'open\', \
                    \'high\', \
                    \'low\', \
                    \'volume\', \
                    \'amount\'"')
    table.on('row-click', lambda row: show_edit(row))
    return table

#
# @description: 显示发票抬头的表格
# @param {list} datas 数据列表
# @param {function} show_edit, show_delete 删除操作的回调函数
#
def show_invoice_title_table(datas, show_edit, show_delete) -> ui.table:
    current_theme = AppContext().theme_manager.get_current_theme()
    bg_color = current_theme.get('background')
    font_color = current_theme.get('font_color', '#c9d1d9')
    widget_border_color = current_theme.get('widget_border_color', '#80808033')
    table_columns = [
        {'name': 'id', 'label': 'id', 'field': 'id', 'width': '0%', 'align': 'center'},
        {'name': 'sn', 'label': '序号', 'field': 'sn', 'width': '5%', 'align': 'center'},
        {'name': 'company_name', 'label': '公司名称', 'field': 'company_name', 'width': '10%', 'align': 'center'},
        {'name': 'address', 'label': '地址', 'field': 'address', 'width': '10%', 'align': 'center'},
        {'name': 'tax_no', 'label': '税号', 'field': 'tax_no', 'width': '5%', 'align': 'center'},
        {'name': 'bank_name', 'label': '银行名称', 'field': 'bank_name', 'width': '10%', 'align': 'center'},
        {'name': 'bank_account', 'label': '银行账户', 'field': 'bank_account', 'width': '10%', 'align': 'center'},
        {'name': 'contact_phone', 'label': '电话', 'field': 'contact_phone', 'width': '5%', 'align': 'center'},
        {'name': 'operation', 'label': '操作', 'field': 'operation', 'width': '10%', 'align': 'center'}
    ]
    with ui.table(
        columns=table_columns,
        rows=datas,
        row_key='id',
        selection='multiple',
        pagination={'rowsPerPage': 10, 'sortBy': 'sn', 'page': 1}) \
            .props(f'table-header-style="color: {font_color}; font-size: 16px; background-color: {bg_color}"') \
            .classes('w-full mt-2 gap-0') \
            .style(f'border: 1px solid {widget_border_color}; border-radius: 10px 10px 0px 0px;') as table:
        table.props('v-model:selected="selected"')
        table.props('visible-columns="[ \
                    \'sn\', \
                    \'company_name\', \
                    \'address\',  \
                    "')
        

        table.add_slot('body-cell-operation', r'''
            <q-td auto-width key="operation" :props="props" class="item-left">
                <q-btn size="sm" flat round dense icon="edit"
                    @click="() => $parent.$emit('show_edit', props.row)"
                />
                &nbsp;
                <q-btn size="sm" flat round dense icon="delete_outline"
                    @click="() => $parent.$emit('show_delete', props.row)"
                />
            </q-td>
        ''')
        
        table.on('show_edit', show_edit)
        table.on('show_delete', show_delete)
    return table

#
# @description: 显示开票的表格
# @param {list} datas 数据列表
#
def show_open_invoice_table(datas, show_edit, show_delete) -> ui.table:
    table_columns = [
        {'name': 'id', 'label': 'id', 'field': 'id', 'width': '1%', 'align': 'center'},
        {'name': 'operation', 'label': '操作', 'field': 'operation', 'width': '10%', 'align': 'center'},
        {'name': 'sn', 'label': '序号', 'field': 'sn', 'width': '5%', 'align': 'center', },
        {'name': 'invoice_time', 'label': '开票时间', 'field': 'invoice_time', 'width': '10%', 'align': 'center'},
        {'name': 'invoice_number', 'label': '发票编号', 'field': 'invoice_number', 'width': '5%', 'align': 'center', 'style': 'position: sticky; left: 0px; background: white; z-index: 2;', 'headerStyle': 'position: sticky; left: 0px;  z-index: 3;'},
        {'name': 'from_company_name', 'label': '开票方', 'field': 'from_company_name', 'width': '10%', 'align': 'center'},
        {'name': 'to_company_name', 'label': '受票方', 'field': 'to_company_name', 'width': '10%', 'align': 'center'},
        {'name': 'status', 'label': '状态', 'field': 'status', 'width': '10%', 'align': 'center'},
        {'name': 'contract_name', 'label': '合同名称', 'field': 'contract_name', 'width': '10%', 'align': 'center'},
        {'name': 'invoice_type', 'label': '发票类型', 'field': 'invoice_type', 'width': '5%', 'align': 'center'},
        {'name': 'invoice_content', 'label': '发票内容', 'field': 'invoice_content', 'width': '5%', 'align': 'center'},
        {'name': 'before_tax_money', 'label': '含税额', 'field': 'before_tax_money', 'width': '10%', 'align': 'center'},
        {'name': 'tax_rate', 'label': '税率', 'field': 'tax_rate', 'width': '5%', 'align': 'center'},
        {'name': 'added_tax', 'label': '增值税', 'field': 'added_tax', 'width': '10%', 'align': 'center'},
        {'name': 'invoice_money', 'label': '税前额', 'field': 'invoice_money', 'width': '10%', 'align': 'center'},
        {'name': 'contract_content', 'label': '合同内容', 'field': 'contract_content', 'width': '10%', 'align': 'center'},
        {'name': 'operator_flag', 'label': '操作模式', 'field': 'operator_flag', 'width': '10%', 'align': 'center'},
        {'name': 'create_time', 'label': '创建时间', 'field': 'create_time', 'width': '10%', 'align': 'center'},
        {'name': 'remark', 'label': '备注', 'field': 'remark', 'width': '10%', 'align': 'center'}
    ]
    with ui.table(
        columns=table_columns,
        rows=datas,
        selection='multiple',
        row_key='id',
        pagination={'rowsPerPage': 10, 'sortBy': 'sn', 'page': 1}) \
            .props('table-header-style="color: white; font-size: 16px; background-color: #65B6FF; position: sticky; top: 0px; background: #65B6FF; z-index: 3"  flat no-shadow') \
            .classes('w-full mt-2 gap-0') \
            .style('border: 1px solid #ECECEC; border-radius: 10px 10px 0px 0px; max-width: 100%; height: calc(100vh - 80px - 80px - 40px)') as table:
        
        table.props('v-model:selected="selected" ')
        table.props('visible-columns="[ \
                    \'sn\', \
                    \'invoice_number\', \
                    \'from_company_name\', \
                    \'to_company_name\', \
                    \'contract_name\', \
                    \'invoice_type\', \
                    \'invoice_content\', \
                    \'before_tax_money\', \
                    \'tax_rate\', \
                    \'invoice_money\', \
                    \'added_tax\', \
                    \'contract_content\', \
                    \'status\', \
                    \'operator_flag\', \
                    \'create_time\', \
                    \'invoice_time\', \
                    \'remark\', \
                    \'operation\']"')

        table.add_slot('body-cell-invoice_type', r'''
            <q-td auto-width key="invoice_type" :props="props">  
                <template v-if="props.row.invoice_type == 0">
                    普票
                </template>
                <template v-if="props.row.invoice_type == 1">
                    专票
                </template>
            </q-td>
        ''')
        table.add_slot('body-cell-tax_rate', r'''
            <q-td auto-width key="tax_rate" :props="props">  
                <template v-if="props.row.tax_rate == 0.01">
                    1%
                </template>
                <template v-if="props.row.tax_rate == 0.03">
                    3%
                </template>
                <template v-if="props.row.tax_rate == 0.06">
                    6%
                </template>
                <template v-if="props.row.tax_rate == 0.09">
                    9%
                </template>
                <template v-if="props.row.tax_rate == 0.13">
                    13%
                </template>
            </q-td>
        ''')
        table.add_slot('body-cell-status', r'''
            <q-td auto-width key="status" :props="props">  
                <template v-if="props.row.status == 0">
                    未开票
                </template>
                <template v-if="props.row.status == 1">
                    已开票
                </template>
                <template v-if="props.row.status == 2">
                    已作废
                </template>
                <template v-if="props.row.status == 3">
                    已冲红
                </template>
            </q-td>
        ''')
        table.add_slot('body-cell-operator_flag', r'''
            <q-td auto-width key="operator_flag" :props="props">  
                <template v-if="props.row.operator_flag == 0">
                    手工操作
                </template>
                <template v-if="props.row.operator_flag == 1">
                    上传发票
                </template>
            </q-td>
        ''')
        table.add_slot('body-cell-operation', r'''
            <q-td auto-width key="operation" :props="props" class="item-left">
                <q-btn size="sm" flat round dense icon="edit"
                    @click="() => $parent.$emit('show_edit', props.row)"
                />
                &nbsp;
                <q-btn size="sm" flat round dense icon="delete_outline"
                    @click="() => $parent.$emit('show_delete', props.row)"
                />
            </q-td>
        ''')
        table.on('show_edit', show_edit)
        table.on('show_delete', show_delete)
    return table

#
# @description: 显示完税证明的表格
# @param {list} datas 数据列表
#
def show_tax_approval_table(datas, show_edit, show_delete) -> ui.table:
    table_columns = [
        {'name': 'operation', 'label': '操作', 'field': 'operation', 'width': '10%', 'align': 'center'},
        {'name': 'sn', 'label': '序号', 'field': 'sn', 'width': '5%', 'align': 'center'},
        {'name': 'create_time', 'label': '填表时间', 'field': 'create_time', 'width': '10%', 'align': 'center'},
        {'name': 'company_name', 'label': '纳税人', 'field': 'company_name', 'width': '10%', 'align': 'center'},
        {'name': 'approval_no', 'label': '完税编号', 'field': 'approval_no', 'width': '10%', 'align': 'center'},
        {'name': 'tax_authority', 'label': '税务机关', 'field': 'tax_authority', 'width': '5%', 'align': 'center'},
        {'name': 'ori_voucher_number', 'label': '原始凭证号码', 'field': 'ori_voucher_number', 'width': '5%', 'align': 'center'},
        {'name': 'tax_type', 'label': '税种', 'field': 'tax_type', 'width': '10%', 'align': 'center'},
        {'name': 'item_name', 'label': '品目名称', 'field': 'item_name', 'width': '5%', 'align': 'center'},
        {'name': 'tax_period', 'label': '税款所属日期', 'field': 'tax_period', 'width': '10%', 'align': 'center'},
        {'name': 'entry_date', 'label': '入库日期', 'field': 'entry_date', 'width': '10%', 'align': 'center'},
        {'name': 'paid_in_money', 'label': '实缴金额', 'field': 'paid_in_money', 'width': '10%', 'align': 'center'},
        {'name': 'total_money', 'label': '总金额', 'field': 'total_money', 'width': '10%', 'align': 'center'},
        {'name': 'remark', 'label': '备注', 'field': 'remark', 'width': '10%', 'align': 'center'}
    ]
    with ui.table(
        columns=table_columns,
        rows=datas,
        selection='multiple',
        row_key='id',
        pagination={'rowsPerPage': 0, 'sortBy': 'sn', 'page': 1}) \
            .props('table-header-style="color: white; font-size: 16px; background-color: #65B6FF;" flat no-shadow') \
            .classes('w-full mt-2 gap-0') \
            .style('border: 1px solid #ECECEC; border-radius: 10px 10px 0px 0px;') as table:
        
        table.props('v-model:selected="selected"')
        table.props('visible-columns="[ \
                    \'sn\', \
                    \'create_time\', \
                    \'company_name\', \
                    \'approval_no\', \
                    \'tax_authority\', \
                    \'ori_voucher_number\', \
                    \'tax_type\', \
                    \'item_name\', \
                    \'tax_period\', \
                    \'entry_date\', \
                    \'paid_in_money\', \
                    \'total_money\', \
                    \'remark\', \
                    \'operation\']"')

        table.add_slot('body-cell-operation', r'''
            <q-td auto-width key="operation" :props="props" class="item-left">
                <q-btn size="sm" flat round dense icon="edit"
                    @click="() => $parent.$emit('show_edit', props.row)"
                />
                &nbsp;
                <q-btn size="sm" flat round dense icon="delete_outline"
                    @click="() => $parent.$emit('show_delete', props.row)"
                />
            </q-td>
        ''')
        table.on('show_edit', show_edit)
        table.on('show_delete', show_delete)
    return table

#
# @description: 显示公司银行账户表格
# @param {list} datas 数据列表
# @param {function} show_delete 删除操作的回调函数
#
def show_company_bank_account_table(datas, show_edit, show_delete) -> ui.table:
    table_columns = [
        {'name': 'id', 'label': 'id', 'field': 'id', 'width': '0%', 'align': 'center'},
        {'name': 'sn', 'label': '序号', 'field': 'sn', 'width': '5%', 'align': 'center'},
        {'name': 'company_name', 'label': '公司名称', 'field': 'company_name', 'width': '10%', 'align': 'center'},
        {'name': 'bank_account', 'label': '银行账户', 'field': 'bank_account', 'width': '10%', 'align': 'center'},
        {'name': 'bank_name', 'label': '银行名称', 'field': 'bank_name', 'width': '10%', 'align': 'center'},
        {'name': 'account_type', 'label': '账号类型', 'field': 'account_type', 'width': '5%', 'align': 'center'},
        {'name': 'opening_balance', 'label': '期初余额', 'field': 'opening_balance', 'width': '10%', 'align': 'right'},
        {'name': 'current_balance', 'label': '当前余额', 'field': 'current_balance', 'width': '10%', 'align': 'right'},
        {'name': 'bank_address', 'label': '银行地址', 'field': 'bank_address', 'width': '20%', 'align': 'center'},
        {'name': 'operation', 'label': '操作', 'field': 'operation', 'width': '10%', 'align': 'center'}
    ]
    with ui.table(
        columns=table_columns,
        rows=datas,
        selection='multiple',
        row_key='id',
        pagination={'rowsPerPage': 10, 'sortBy': 'sn', 'page': 1}) \
            .props('table-header-style="color: white; font-size: 16px; background-color: #65B6FF;"') \
            .classes('w-full mt-2 gap-0') \
            .style('border: 1px solid #ECECEC; border-radius: 10px 10px 0px 0px;') as table:
        table.props('v-model:selected="selected"')
        table.props('visible-columns="[\'sn\', \
                    \'company_name\', \
                    \'bank_account\', \
                    \'bank_name\', \
                    \'account_type\', \
                    \'opening_balance\', \
                    \'current_balance\', \
                    \'bank_address\', \
                    \'operation\']"')
        
        table.add_slot('body-cell-account_type', r'''
            <q-td auto-width key="account_type" :props="props">  
                <template v-if="props.row.account_type == 0">
                    基本户
                </template>
                <template v-if="props.row.account_type == 1">
                    一般户
                </template>
            </q-td>
        ''')

        table.add_slot('body-cell-operation', r'''
            <q-td auto-width key="operation" :props="props" class="item-left">
                <q-btn size="sm" flat round dense icon="edit"
                    @click="() => $parent.$emit('show_edit', props.row)"
                />
                &nbsp;
                <q-btn size="sm" flat round dense icon="delete_outline"
                    @click="() => $parent.$emit('show_delete', props.row)"
                />
            </q-td>
        ''')
        table.on('show_edit', show_edit)
        table.on('show_delete', show_delete)
    return table

#
# @description: 显示付款记录表格
# @param {list} datas 数据列表
#
def show_payment_record_table(datas, show_edit, show_delete) -> ui.table:
    table_columns = [
        {'name': 'id', 'label': 'id', 'field': 'id', 'width': '0%', 'align': 'center'},
        {'name': 'sn', 'label': '序号', 'field': 'sn', 'width': '5%', 'align': 'center'},
        {'name': 'from_company_name', 'label': '付款方', 'field': 'from_company_name', 'width': '10%', 'align': 'center'},
        {'name': 'to_company_name', 'label': '收款方', 'field': 'to_company_name', 'width': '10%', 'align': 'center'},
        {'name': 'contract_name', 'label': '合同名称', 'field': 'contract_name', 'width': '5%', 'align': 'center'},
        {'name': 'from_bank_name', 'label': '付款银行账户', 'field': 'from_bank_name', 'width': '10%', 'align': 'center'},
        {'name': 'to_bank_name', 'label': '收款银行账户', 'field': 'to_bank_name', 'width': '10%', 'align': 'center'},
        {'name': 'payment_money', 'label': '付款金额', 'field': 'payment_money', 'width': '5%', 'align': 'right'},
        {'name': 'item_name', 'label': '事项', 'field': 'item_name', 'width': '5%', 'align': 'center'},
        # {'name': 'should_invoice_money', 'label': '应开票金额', 'field': 'should_invoice_money', 'width': '10%', 'align': 'center'},
        # {'name': 'has_invoice_money', 'label': '已开票金额', 'field': 'has_invoice_money', 'width': '10%', 'align': 'center'},
        # {'name': 'remain_invoice_money', 'label': '未开票金额', 'field': 'remain_invoice_money', 'width': '10%', 'align': 'center'},
        {'name': 'status', 'label': '状态', 'field': 'status', 'width': '5%', 'align': 'center'},
        {'name': 'remarks', 'label': '备注', 'field': 'remarks', 'width': '10%', 'align': 'center'},
        {'name': 'create_time', 'label': '付款时间', 'field': 'create_time', 'width': '10%', 'align': 'center'},
        {'name': 'operation', 'label': '操作', 'field': 'operation', 'width': '10%', 'align': 'center'}
    ]
    with ui.table(
        columns=table_columns,
        rows=datas,
        selection='multiple',
        row_key='id',
        pagination={'rowsPerPage': 10, 'sortBy': 'sn', 'page': 1}) \
            .props('table-header-style="color: white; font-size: 16px; background-color: #65B6FF;" flat no-shadow') \
            .classes('w-full mt-2 gap-0') \
            .style('border: 1px solid #ECECEC; border-radius: 10px 10px 0px 0px;') as table:
        
        table.props('v-model:selected="selected"')
        table.props('visible-columns="[ \
                    \'sn\', \
                    \'from_company_name\', \
                    \'to_company_name\', \
                    \'contract_name\', \
                    \'from_bank_name\', \
                    \'to_bank_name\', \
                    \'payment_money\', \
                    \'item_name\', \
                    \'status\', \
                    \'create_time\', \
                    \'remarks\', \
                    \'operation\']"')

        table.add_slot('body-cell-status', r'''
            <q-td auto-width key="status" :props="props">  
                <template v-if="props.row.status == 0">
                    未完成
                </template>
                <template v-if="props.row.status == 1">
                    已完成
                </template>
                <template v-if="props.row.status == 2">
                    已取消
                </template>
            </q-td>
        ''')
        table.add_slot('body-cell-operation', r'''
            <q-td auto-width key="operation" :props="props" class="item-left">
                <q-btn size="sm" flat round dense icon="edit"
                    @click="() => $parent.$emit('show_edit', props.row)"
                />
                &nbsp;
                <q-btn size="sm" flat round dense icon="delete_outline"
                    @click="() => $parent.$emit('show_delete', props.row)"
                />
            </q-td>
        ''')
        table.on('show_edit', show_edit)
        table.on('show_delete', show_delete)
    return table    

#
# @description: 显示业务记录表格
# @param {list} datas 数据列表
#
def show_service_record_table(datas, show_edit, show_delete) -> ui.table:
    table_columns = [
        {'name': 'id', 'label': 'id', 'field': 'id', 'width': '0%', 'align': 'center'},
        {'name': 'sn', 'label': '序号', 'field': 'sn', 'width': '5%', 'align': 'center'},
        {'name': 'operation', 'label': '操作', 'field': 'operation', 'width': '5%', 'align': 'center'},
        {'name': 'from_company_name', 'label': '甲方', 'field': 'from_company_name', 'width': '10%', 'align': 'center'},
        {'name': 'to_company_name', 'label': '乙方', 'field': 'to_company_name', 'width': '10%', 'align': 'center'},
        {'name': 'contract_name', 'label': '合同名称', 'field': 'contract_name', 'width': '10%', 'align': 'center'},
        {'name': 'contract_content', 'label': '合同内容', 'field': 'contract_name', 'width': '10%', 'align': 'center'},
        {'name': 'contract_money', 'label': '合同金额', 'field': 'contract_money', 'width': '5%', 'align': 'right'},
        {'name': 'is_contract', 'label': '是否有合同', 'field': 'is_contract', 'width': '5%', 'align': 'center'},
        {'name': 'invoice_money', 'label': '开票金额', 'field': 'invoice_money', 'width': '5%', 'align': 'right'},
        {'name': 'payment_money', 'label': '付款金额', 'field': 'payment_money', 'width': '5%', 'align': 'right'},
        {'name': 'invoice_gap_money', 'label': '发票差额', 'field': 'invoice_gap_money', 'width': '5%', 'align': 'right'},
        {'name': 'payment_gap_money', 'label': '付款差额', 'field': 'payment_gap_money', 'width': '5%', 'align': 'right'},
        {'name': 'sync_time', 'label': '同步时间', 'field': 'sync_time', 'width': '10%', 'align': 'center'},
        {'name': 'latest_payment_time', 'label': '最近付款时间', 'field': 'latest_payment_time', 'width': '10%', 'align': 'center'},
        {'name': 'latest_invoice_time', 'label': '最近开票时间', 'field': 'latest_invoice_time', 'width': '10%', 'align': 'center'},
        {'name': 'status', 'label': '状态', 'field': 'status', 'width': '5%', 'align': 'center'},
        {'name': 'create_time', 'label': '创建时间', 'field': 'create_time', 'width': '10%', 'align': 'center'}
    ]
    with ui.table(
        columns=table_columns,
        rows=datas,
        selection='multiple',
        row_key='id',
        pagination={'rowsPerPage': 10, 'sortBy': 'sn', 'page': 1}) \
            .props('table-header-style="color: white; font-size: 16px; background-color: #65B6FF;" flat no-shadow') \
            .classes('w-full mt-2 gap-0') \
            .style('border: 1px solid #ECECEC; border-radius: 10px 10px 0px 0px;') as table:
        
        table.props('v-model:selected="selected"')
        table.props('visible-columns="[ \
                    \'sn\', \
                    \'from_company_name\', \
                    \'to_company_name\', \
                    \'contract_name\', \
                    \'contract_content\', \
                    \'contract_money\', \
                    \'is_contract\', \
                    \'remain_invoice_money\', \
                    \'invoice_money\', \
                    \'payment_money\', \
                    \'invoice_gap_money\', \
                    \'payment_gap_money\', \
                    \'sync_time\', \
                    \'latest_payment_time\', \
                    \'latest_invoice_time\', \
                    \'status\', \
                    \'create_time\', \
                    \'operation\']"')

        table.add_slot('body-cell-is_contract', r'''
            <q-td auto-width key="is_contract" :props="props">  
                <template v-if="props.row.is_contract == 0">
                    无
                </template>
                <template v-if="props.row.is_contract == 1">
                    有
                </template>
            </q-td>
        ''')
        table.add_slot('body-cell-status', r'''
            <q-td auto-width key="status" :props="props">  
                <template v-if="props.row.status == 0">
                    无
                </template>
                <template v-if="props.row.status == 1">
                    无合同
                </template>
                <template v-if="props.row.status == 2">
                    待付款
                </template>
                <template v-if="props.row.status == 3">
                    待开票
                </template>
                <template v-if="props.row.status == 4">
                    完成
                </template>
            </q-td>
        ''')
        table.add_slot('body-cell-operation', r'''
            <q-td auto-width key="operation" :props="props" class="item-left">
                <q-btn size="sm" flat round dense icon="edit"
                    @click="() => $parent.$emit('show_edit', props.row)"
                />
                &nbsp;
                <q-btn size="sm" flat round dense icon="delete_outline"
                    @click="() => $parent.$emit('show_delete', props.row)"
                />
            </q-td>
        ''')
        table.on('show_edit', show_edit)
        table.on('show_delete', show_delete)
    return table    

#
# @description: 显示期初数据表格
# @param {list} datas 数据列表
#
def show_period_data_table(datas, show_edit, show_delete) -> ui.table:
    table_columns = [
        {'name': 'id', 'label': 'id', 'field': 'id', 'width': '0%', 'align': 'center'},
        {'name': 'sn', 'label': '序号', 'field': 'sn', 'width': '5%', 'align': 'center'},
        {'name': 'company_name', 'label': '公司名称', 'field': 'company_name', 'width': '10%', 'align': 'center'},
        {'name': 'create_time', 'label': '日期', 'field': 'create_time', 'width': '10%', 'align': 'center'},
        {'name': 'last_month_no_verify', 'label': '上月未认证', 'field': 'last_month_no_verify', 'width': '10%', 'align': 'right'},
        {'name': 'last_month_stay_pay', 'label': '上月留抵', 'field': 'last_month_stay_pay', 'width': '10%', 'align': 'right'},
        {'name': 'billing_amount', 'label': '开票额', 'field': 'billing_amount', 'width': '10%', 'align': 'right'},
        {'name': 'operation', 'label': '操作', 'field': 'operation', 'width': '5%', 'align': 'center'}
    ]
    with ui.table(
        columns=table_columns,
        rows=datas,
        selection='multiple',
        row_key='id',
        pagination={'rowsPerPage': 10, 'sortBy': 'sn', 'page': 1}) \
            .props('table-header-style="color: white; font-size: 16px; background-color: #65B6FF;" flat no-shadow') \
            .classes('w-full mt-2 gap-0') \
            .style('border: 1px solid #ECECEC; border-radius: 10px 10px 0px 0px;') as table:
        
        table.props('v-model:selected="selected"')
        table.props('visible-columns="[ \
                    \'sn\', \
                    \'company_name\', \
                    \'create_time\', \
                    \'last_month_no_verify\', \
                    \'last_month_stay_pay\', \
                    \'billing_amount\', \
                    \'operation\']"')

        table.add_slot('body-cell-operation', r'''
            <q-td auto-width key="operation" :props="props" class="item-left">
                <q-btn size="sm" flat round dense icon="edit"
                    @click="() => $parent.$emit('show_edit', props.row)"
                />
                &nbsp;
                <q-btn size="sm" flat round dense icon="delete_outline"
                    @click="() => $parent.$emit('show_delete', props.row)"
                />
            </q-td>
        ''')
        table.on('show_edit', show_edit)
        table.on('show_delete', show_delete)
    return table

#
# @description: 显示增值税数据表格
# @param {list} datas 数据列表
#
def show_value_added_table(datas, show_edit, show_delete) -> ui.table:
    table_columns = [
        {'name': 'id', 'label': 'id', 'field': 'id', 'width': '0%', 'align': 'center'},
        {'name': 'operation', 'label': '操作', 'field': 'operation', 'width': '5%', 'align': 'center'},
        {'name': 'sn', 'label': '序号', 'field': 'sn', 'width': '5%', 'align': 'center'},
        {'name': 'company_name', 'label': '公司名称', 'field': 'company_name', 'align': 'center', 'style': 'position: sticky; width: 100px; left: 0px; background: white; z-index: 2;', 'headerStyle': 'position: sticky; left: 0px;  z-index: 3;'},
        {'name': 'create_time', 'label': '日期', 'field': 'create_time', 'width': '10%', 'align': 'center', 'style': 'position: sticky; left: 80px; background: white; z-index: 2;', 'headerStyle': 'position: sticky; left: 80px;  z-index: 3;'},
        {'name': 'last_month_no_verify', 'label': '上月未认证', 'field': 'last_month_no_verify', 'width': '10%', 'align': 'right'},
        {'name': 'last_month_stay_pay', 'label': '上月留抵', 'field': 'last_month_stay_pay', 'width': '10%', 'align': 'right'},
        {'name': 'opened_input_tax', 'label': '已开进项税', 'field': 'opened_input_tax', 'width': '10%', 'align': 'right'},
        {'name': 'opened_output_tax', 'label': '已开销项税', 'field': 'opened_output_tax', 'width': '10%', 'align': 'right'},
        {'name': 'to_open_input_tax', 'label': '待开进项税', 'field': 'to_open_input_tax', 'width': '10%', 'align': 'right'},
        {'name': 'to_open_output_tax', 'label': '待开销项税', 'field': 'to_open_output_tax', 'width': '10%', 'align': 'right'},
        {'name': 'payable_tax', 'label': '应纳税额', 'field': 'payable_tax', 'width': '10%', 'align': 'right'},
        {'name': 'sales_amount', 'label': '6%开销售额', 'field': 'sales_amount', 'width': '10%', 'align': 'right'},
        {'name': 'opened_billing_amount', 'label': '已开票额', 'field': 'opened_billing_amount', 'width': '10%', 'align': 'right'},
        {'name': 'remaining_billing_amount', 'label': '剩余开票额', 'field': 'remaining_billing_amount', 'width': '10%', 'align': 'right'},
        {'name': 'billing_amount', 'label': '开票额', 'field': 'billing_amount', 'width': '10%', 'align': 'right'}
    ]
    with ui.table(
        columns=table_columns,
        rows=datas,
        selection='multiple',
        row_key='id',
        pagination={'rowsPerPage': 0, 'sortBy': 'sn', 'page': 1}) \
            .props('table-header-style="color: white; font-size: 16px; background-color: #65B6FF; position: sticky; top: 0px; background: #65B6FF; z-index: 3" flat no-shadow') \
            .classes('w-full mt-2 gap-0') \
            .style('border: 1px solid #ECECEC; border-radius: 10px 10px 0px 0px; max-width: 100%; height: calc(100vh - 80px - 80px - 40px)') as table:
        
        table.props('v-model:selected="selected"')
        table.props('visible-columns="[ \
                    \'sn\', \
                    \'company_name\', \
                    \'create_time\', \
                    \'last_month_no_verify\', \
                    \'last_month_stay_pay\', \
                    \'opened_input_tax\', \
                    \'opened_output_tax\', \
                    \'to_open_input_tax\', \
                    \'to_open_output_tax\', \
                    \'payable_tax\', \
                    \'sales_amount\', \
                    \'opened_billing_amount\', \
                    \'remaining_billing_amount\', \
                    \'billing_amount\', \
                    \'operation\']"')

        table.add_slot('body-cell-operation', r'''
            <q-td auto-width key="operation" :props="props" class="item-left">
                <q-btn size="sm" flat round dense icon="edit"
                    @click="() => $parent.$emit('show_edit', props.row)"
                />
                &nbsp;
                <q-btn size="sm" flat round dense icon="delete_outline"
                    @click="() => $parent.$emit('show_delete', props.row)"
                />
            </q-td>
        ''')
        table.on('show_edit', show_edit)
        table.on('show_delete', show_delete)
    return table

#
# @description: 显示开票预警信息
# @param {list} datas 数据列表
#
def show_open_invoice_alarm_table(datas) -> ui.table:
    table_columns = [
        {'name': 'id', 'label': 'id', 'field': 'id', 'width': '1%', 'align': 'center'},
        {'name': 'sn', 'label': '序号', 'field': 'sn', 'width': '5%', 'align': 'center', },
        {'name': 'company_name', 'label': '开票方', 'field': 'company_name', 'width': '10%', 'align': 'center'},
        {'name': 'alarm_desc', 'label': '预警类型', 'field': 'alarm_desc', 'width': '10%', 'align': 'center'},
        {'name': 'invoice_year', 'label': '年份', 'field': 'invoice_year', 'width': '5%', 'align': 'center'},
        {'name': 'detail', 'label': '详情', 'field': 'detail', 'width': '20%', 'align': 'left'},
        {'name': 'create_time', 'label': '创建时间', 'field': 'create_time', 'width': '10%', 'align': 'center'}]
    with ui.table(
        columns=table_columns,
        rows=datas,
        selection='multiple',
        row_key='id',
        pagination={'rowsPerPage': 10, 'sortBy': 'sn', 'page': 1}) \
            .props('table-header-style="color: white; font-size: 16px; background-color: #65B6FF; position: sticky; top: 0px; background: #65B6FF; z-index: 3"  flat no-shadow') \
            .classes('w-full mt-2 gap-0') \
            .style('border: 1px solid #ECECEC; border-radius: 10px 10px 0px 0px; max-width: 100%; height: calc(100vh - 80px - 80px - 40px)') as table:
        
        table.props('v-model:selected="selected" ')
        table.props('visible-columns="[ \
                    \'sn\', \
                    \'company_name\', \
                    \'alarm_desc\', \
                    \'invoice_year\', \
                    \'detail\', \
                    \'create_time\']"')

    return table