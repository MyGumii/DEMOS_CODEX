"""UI-PARENT-HISTORY: normal child action repairs certified older divergence."""
import pytest
from code_assist_v1.tests.browser.test_notion_parent_structure_rebase_browser import _run_parent_structure_scenario


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width', [('light', 1280), ('dark', 390)])
def test_real_sidebar_child_creation_retains_historical_delete_order_and_rich_text(tmp_path, theme, width):
    _run_parent_structure_scenario(tmp_path, theme, width, historical=True)
