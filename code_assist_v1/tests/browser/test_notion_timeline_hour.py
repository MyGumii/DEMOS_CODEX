"""UI-R5: the timeline hour scale places timed and all-day items."""

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page


@pytest.mark.integration
@pytest.mark.regression
def test_ui_timeline_hour_scale_has_quarter_hour_slots_and_time_bars(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.evaluate("""() => {
      const today=localIsoDate(new Date());
      const tomorrow=new Date();tomorrow.setDate(tomorrow.getDate()+1);
      State.pageData.blocks=[EditorCore.createBlock('timeline',{content:'시간 비교',props:{
        properties:[{id:'title',name:'이름',type:'title'},
          {id:'date',name:'날짜',type:'date'}],
        rows:[
          {title:'시간 지정',date:{start:today,end:today,time:'17:00',end_time:'17:30'}},
          {title:'하루 종일',date:today},
          {title:'다음 날',date:{start:localIsoDate(tomorrow),time:'00:15'}},
        ],
        views:[{id:'hour-view',name:'시간 비교',type:'timeline'}],
        active_view_id:'hour-view',timeline_date_property:'date',
        timeline_anchor:today,timeline_scale:'hour',
      }})];
      renderBlocks();
    }""")
    timeline = page.locator('#blocks .timeline-block.hour-scale')
    expect(timeline).to_be_visible()
    expect(timeline.locator('[data-timeline-scale]')).to_have_value('hour')
    expect(timeline.locator('.timeline-header .timeline-slot-label')).to_have_count(192)
    expect(timeline.locator('.timeline-header .timeline-day-label')).to_have_count(2)
    expect(timeline.locator('.timeline-slot-label').first).to_have_text('오전 12:00')
    expect(timeline.locator('.timeline-slot-label').last).to_have_text('오후 11:45')
    expect(timeline.locator('.timeline-row')).to_have_count(3)
    bars = timeline.locator('.timeline-row .timeline-bar')
    assert 'grid-column:70 / span 3' in bars.nth(0).get_attribute('style')
    assert 'grid-column:2 / span 96' in bars.nth(1).get_attribute('style')
    assert 'grid-column:99 / span 1' in bars.nth(2).get_attribute('style')
    expect(timeline.locator('.timeline-header .timeline-now-line')).to_have_count(1)
    position = timeline.locator('.timeline-scroll').evaluate("""scroll => ({
      left:scroll.scrollLeft,width:scroll.clientWidth,total:scroll.scrollWidth,
      slot:Number(scroll.dataset.timelineNowSlot),
    })""")
    assert position['total'] > position['width']
    expected = min(position['total'] - position['width'],
                   max(0, position['slot'] * 72 - position['width'] * .8))
    assert abs(position['left'] - expected) < 2
    page.evaluate("""() => {
      document.querySelector('#blocks .timeline-block.hour-scale .timeline-scroll').scrollLeft=1000;
      renderBlocks();
    }""")
    assert abs(timeline.locator('.timeline-scroll').evaluate('scroll => scroll.scrollLeft')
               - 1000) < 2


@pytest.mark.integration
@pytest.mark.regression
def test_ui_linked_timeline_hour_scroll_survives_source_refresh(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.evaluate("""async () => {
      const today=localIsoDate(new Date());
      const node=document.createElement('div');
      node.id='linked-hour-probe';document.body.append(node);
      const block=NotionLinkedDatabases.descriptor('linked-hour-probe',
        {document_id:'source-page',id:'source-database'},
        {type:'timeline',name:'시간 비교',config:{timeline_anchor:today,
          timeline_scale:'hour',timeline_date_property:'date'}});
      const host={
        current:()=>true,visible:()=>true,canEdit:()=>true,canEditCells:()=>true,
        load:async()=>({database:{title:'연결 원본'},source:{document_id:'source-page'}}),
        project:()=>{const projected=EditorCore.createBlock('timeline',{content:'시간 비교',props:{
          properties:[{id:'title',name:'이름',type:'title'},
            {id:'date',name:'날짜',type:'date'}],
          rows:[{title:'연결 시간',date:{start:today,time:'17:00'}}],
          timeline_anchor:today,timeline_scale:'hour',timeline_date_property:'date'}});
          projected.id=block.id;return projected;},
        render:(projected,editable)=>databaseBlockHtml(projected,editable),
        dates:NotionDatabaseDates,commit:()=>true,open:()=>{},
      };
      const view=NotionLinkedDatabases.create(host);
      window.linkedHourView=view;
      await view.mount({pageId:'probe',workspaceId:'probe'},[{id:block.id,block,node}]);
    }""")
    scroll = page.locator('#linked-hour-probe .timeline-block.hour-scale .timeline-scroll')
    expect(scroll).to_be_visible()
    page.evaluate("""async () => {
      document.querySelector('#linked-hour-probe .timeline-scroll').scrollLeft=1000;
      await window.linkedHourView.refresh(true);
    }""")
    assert abs(scroll.evaluate('element => element.scrollLeft') - 1000) < 2
    scroll.evaluate('element => element.scrollLeft=element.scrollWidth')
    page.wait_for_function("""() => {
      const scroll=document.querySelector('#linked-hour-probe .timeline-scroll');
      return scroll?.dataset.timelineStart!==localIsoDate(new Date());
    }""")
    tomorrow = page.evaluate("""() => {
      const day=new Date();day.setDate(day.getDate()+1);return localIsoDate(day);
    }""")
    assert scroll.get_attribute('data-timeline-start') == tomorrow


@pytest.mark.integration
@pytest.mark.regression
def test_ui_hour_undated_assignment_uses_scrolled_visible_hour(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.evaluate("""() => {
      const today=localIsoDate(new Date());
      State.pageData.blocks=[EditorCore.createBlock('timeline',{content:'시간 지정',props:{
        properties:[{id:'title',name:'이름',type:'title'},
          {id:'date',name:'날짜',type:'date'}],
        rows:[{title:'첫 항목'},{title:'둘째 항목'}],
        views:[{id:'hour-view',name:'시간 지정',type:'timeline'}],
        active_view_id:'hour-view',timeline_date_property:'date',
        timeline_anchor:today,timeline_scale:'hour',
      }})];
      renderBlocks();
      document.querySelector('#blocks .timeline-block.hour-scale .timeline-scroll').scrollLeft=29*72;
    }""")
    timeline = page.locator('#blocks .timeline-block.hour-scale')
    timeline.locator('.database-undated summary').click()
    timeline.locator('[data-undated-assign="0"]').click()
    expect(timeline.locator('.database-undated[open] summary')).to_contain_text('날짜 없음(1)')
    expect(timeline.locator('.timeline-row')).to_have_count(1)
    today = page.evaluate('() => localIsoDate(new Date())')
    assert page.evaluate('() => State.pageData.blocks[0].props.rows[0].date') == {
        'start': today, 'end': today, 'time': '08:15', 'end_time': '09:15'}
    assert abs(timeline.locator('.timeline-scroll').evaluate('scroll => scroll.scrollLeft')
               - 29 * 72) < 2


@pytest.mark.integration
@pytest.mark.regression
def test_ui_hour_timeline_scrolls_across_days_without_changing_saved_anchor(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.evaluate("""() => {
      const today=localIsoDate(new Date());
      State.pageData.blocks=[EditorCore.createBlock('timeline',{content:'연속 시간',props:{
        properties:[{id:'title',name:'이름',type:'title'},
          {id:'date',name:'날짜',type:'date'}],
        rows:[{title:'기준 항목',date:{start:today,time:'17:00'}},
          {title:'날짜 없는 항목'}],
        views:[{id:'hour-view',name:'연속 시간',type:'timeline'}],
        active_view_id:'hour-view',timeline_date_property:'date',
        timeline_anchor:today,timeline_scale:'hour',
      }})];
      renderBlocks();
      window.hourWindowOriginalAnchor=today;
    }""")
    scroll = page.locator('#blocks .timeline-block.hour-scale .timeline-scroll')
    expect(scroll).to_be_visible()
    assert scroll.get_attribute('data-timeline-start') == page.evaluate(
        '() => window.hourWindowOriginalAnchor')
    scroll.evaluate('element => element.scrollLeft=element.scrollWidth')
    page.wait_for_function("""() => document.querySelector(
      '#blocks .timeline-block.hour-scale .timeline-scroll')?.dataset.timelineStart
      !== window.hourWindowOriginalAnchor""")
    next_day = page.evaluate("""() => {
      const day=new Date();day.setDate(day.getDate()+1);return localIsoDate(day);
    }""")
    assert scroll.get_attribute('data-timeline-start') == next_day
    assert page.evaluate('() => State.pageData.blocks[0].props.timeline_anchor') == (
        page.evaluate('() => window.hourWindowOriginalAnchor'))
    scroll.evaluate('element => element.scrollLeft=29*72')
    timeline = page.locator('#blocks .timeline-block.hour-scale')
    timeline.locator('.database-undated summary').click()
    timeline.locator('[data-undated-assign="1"]').click()
    assert page.evaluate('() => State.pageData.blocks[0].props.rows[1].date') == {
        'start': next_day, 'end': next_day, 'time': '08:15', 'end_time': '09:15'}
    scroll.evaluate('element => element.scrollLeft=element.scrollWidth')
    following_day = page.evaluate("""() => {
      const day=new Date();day.setDate(day.getDate()+2);return localIsoDate(day);
    }""")
    page.wait_for_function("""following => document.querySelector(
      '#blocks .timeline-block.hour-scale .timeline-scroll')?.dataset.timelineStart
      === following""", arg=following_day)
    assert scroll.get_attribute('data-timeline-start') == following_day
    scroll.evaluate('element => element.scrollLeft=0')
    page.wait_for_function("""previous => document.querySelector(
      '#blocks .timeline-block.hour-scale .timeline-scroll')?.dataset.timelineStart
      === previous""", arg=next_day)
    scroll.evaluate('element => element.scrollLeft=0')
    page.wait_for_function("""() => document.querySelector(
      '#blocks .timeline-block.hour-scale .timeline-scroll')?.dataset.timelineStart
      === window.hourWindowOriginalAnchor""")
    assert scroll.locator('.timeline-header .timeline-day-label').count() == 2

