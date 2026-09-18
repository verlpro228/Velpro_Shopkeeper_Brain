import asyncio
import json
from typing import Dict, List, Tuple, Any

from agents.mcp import MCPServerStreamableHttp

from knowledge.processor.query_process.base import BaseNode
from knowledge.processor.query_process.exceptions import StateFieldError
from knowledge.processor.query_process.state import QueryGraphState


class WebSearchMcpNode(BaseNode):
  name = "web_search_mcp"

  def process(self, state: QueryGraphState) -> Dict[str, Any]:
    """
    三路检索之一： 基于web联网的MCP服务调用。
    :param state: rewritten_query,item_names
    :return:
        {
            "web_search_docs": []
        }
    """

    # 1.参数校验
    validateed_rewritten_query, validated_item_names = self._validate_input(state)

    # 2.创建MCP客户端
    # 异步函数调用放在事件循环中，变成同步处理
    web_search_docs = asyncio.run(self._web_mcp(validateed_rewritten_query))
    if not web_search_docs:
      return {"web_search_docs": []}

    # 5.封装返回结果
    return {
      "web_search_docs": web_search_docs
    }

  def _validate_input(self, state) -> Tuple[str, List[str]]:
    # 1.参数校验
    rewritten_query = state.get("rewritten_query")
    item_names = state.get("item_names")

    if not rewritten_query or not isinstance(rewritten_query, str):
      self.logger.error(f"Invalid rewritten_query: {rewritten_query}")
      raise StateFieldError(self.name, "rewritten_query", str)


    if not item_names or not isinstance(item_names, list):
      self.logger.error(f"Invalid item_names: {item_names}")
      raise StateFieldError(self.name, "item_names", str)
    return rewritten_query, item_names

  async def _web_mcp(self, validateed_rewritten_query):

    # 1.获取MCP客户端
    async with MCPServerStreamableHttp(
        name="网络搜索",
        params={
          "url": self.config.mcp_dashscope_base_url,  # MCP 服务端点
          "headers": {"Authorization": f"Bearer {self.config.dashscope_api_key}"},  # 认证头
          "timeout": 300,  # 请求超时时间（秒）
          "terminate_on_close": True,  # 关闭时终止连接
        },
        max_retry_attempts=2,  # 最大重试次数
        client_session_timeout_seconds=30,  # 客户端会话超时时间（秒）
        cache_tools_list=True  # 缓存工具列表，避免重复请求
    ) as client:

      # 3.调用 call_tool    bailian_web_search
      execute_tool_result = await client.call_tool(
        tool_name="bailian_web_search",
        arguments={"query": validateed_rewritten_query, "count": 3}
      )
      print(execute_tool_result)
      # 4.解析结果（逐层判空，任一层为空 → [] 走降级，不抛异常）
      # MCP 返回结构: CallToolResult.content 是 TextContent 列表，取 [0].text 即 JSON 字符串
      if not execute_tool_result or not execute_tool_result.content or not execute_tool_result.content[0]:
        return []
      # content[0].text 是 JSON 字符串，需二次 loads 得到 dict
      text_json = json.loads(execute_tool_result.content[0].text)
      if not text_json:
        return []
      # 业务字段: text_json.pages → 命中网页列表（每条含 snippet/title/url/hostname）
      pages = text_json.get("pages")
      if not pages:
        return []
      # 仅保留 snippet/title/url 三字段（hostlogo/hostname 等冗余字段剔除，便于后续直接拼 prompt）
      web_search_docs = []
      for page in pages:
        snippet = page.get("snippet")
        title = page.get("title")
        url = page.get("url")
        web_search_docs.append(
          {
            "snippet": snippet,
            "title": title,
            "url": url
          }
        )
      return web_search_docs


if __name__ == '__main__':

  state = {
    # "rewritten_query": "今天的小米汽车的股价是多少",
    "rewritten_query": "万用表如何测量电阻",
    "item_names": ["RS-12 数字万用表"]
  }

  web_mcp_search = WebSearchMcpNode()
  result = web_mcp_search(state)

  for r in result.get('web_search_docs', []):
    print(json.dumps(r, ensure_ascii=False, indent=2))

"""
meta=None content=[TextContent(type='text', text='{"pages":[{"snippet":"小米汽车(BK0888) 简介:小米汽车 今开:899.56 讨论 若水棋局_朱永红06-15 05:36 $小米集团-W(01810)$预定小米su7的过程,让我看清了这家公司的底色,损失了5000元定金,但帮我排除了以后投资这家公司可能带来的潜在风险。 手机可以堆料,汽车却需要沉淀,不仅仅是技术层面和科技层面的沉淀,更需要安全和可靠性层面的沉淀。 现在我终于想清楚了,小米投资汽车,从战略","hostname":"无","hostlogo":"https://b.bdstatic.com/searchbox/mappconsole/image/20190805/1239163c-77cc-449e-b91f-9bb1d27e43a7.png","title":"小米汽车(BK0888)","url":"https://xueqiu.com/S/BK0888/relevant"},{"snippet":"小米股价过山车:6000亿说没就没,“一夜”回到造车前 知嘹汽车/费德 小米的股价,又坐了一轮刺激的过山车。进入2026年,当人们把目光投向这家话题不断的公司时,发现其股价已经悄悄跌回了35港元以下,市值徘徊在9000亿港元出头。 这个数字,眼熟得很——差不多就是2024年底小米汽车还没发布时的水平。换句话说,过去一年里一度暴涨的那约6000亿港元市值,已经蒸发得干干净净。资本市场给小米讲的那个宏大造车故事,似乎突然被按下了静音键。 这不仅仅是数字游戏,更直接反映在全球车企的江湖排位上。小米市值曾经一度冲到全球车企第三,风光无限,但现在又被比亚迪反超,跌回了第四。 回看小米市值的狂飙,2023年,小米还是个市值2000多亿港元的手机公司。随着2024年小米SU7(参数|询价|图片)的横空出世,资本市场瞬间沸腾,市值最高冲破了1.5万亿港元。一年多涨了四倍,这哪里是估值?这分明是资本圈为它的跨界造车梦提前预支的溢价。 然而,梦做得太美,醒得也就越突然。2025年站上高峰的小米,最先冒出来的是一堆产品和口碑上的挑战。比如,SU7 Ultra那个听起来很厉害的碳纤维机盖,实际功能和宣传的好像不太一样,甚至为此闹上了法庭。接着,尾灯开裂的品控问题、让车主提前结清尾款的销售政策等等问题,接二连三地冒出来,让不少消费者觉得心里不舒服。 真正砸下重锤的是2026年1月,一天之内连着传出两起涉及小米车辆的事件,虽然公司火速回应说数据正常,但这种消息多了,多少让人心存忧虑。 所以,从1.5万亿高点跌掉将近40%,回到9000亿,这既是市场情绪的退潮,也是投资者在重新审视小米。不过,就算跌了这么多,跟造车前的2000多亿市值比,小米现在还是涨了两倍多。这说明,造车这件事,确实从根本上拔高了大家对它的想象空间。股价的起伏是常态,关键还得看它业务的基本盘能不能接得住这份期待。 核心就看两块——手机和汽车。手机板块,小米在全球市场的基本盘还算稳,冲高端的路子也在继续走。汽车这边,故事则更有戏剧性。2025年第二季度,小米汽车交了8万多台,毛利率还提升到了26%以上;到了第三季度,竟然真的实现了单季盈利。这份成绩单,让一些机构又燃起了希望,甚至预测它2026年销量能翻着跟头涨。","hostname":"易车网","hostlogo":"https://ss2.baidu.com/6ONYsjip0QIZ8tyhnq/it/u=3557303210,2734739739&fm=195&app=88&f=JPEG?w=200&h=200","title":"小米股价过山车:6000亿说没就没,“一夜”回到造车前","url":"https://news.m.yiche.com/hao/wenzhang/106995298/"},{"snippet":"小米汽车(BK0888) 简介:小米汽车","hostname":"雪球","hostlogo":"https://img.alicdn.com/imgextra/i2/O1CN01n9Ac7I1CnITyH2vsW_!!6000000000125-55-tps-32-32.svg","title":"小米汽车(BK0888)","url":"https://www.xueqiu.com/S/BK0888/notices"}],"request_id":"6a7c6b51-a5d1-97a9-b4ca-8ec487cf1152","status":0}', annotations=None, meta=None)] structuredContent=None isError=False
{
  "snippet": "小米汽车(BK0888) 简介:小米汽车 今开:899.56 讨论 若水棋局_朱永红06-15 05:36 $小米集团-W(01810)$预定小米su7的过程,让我看清了这家公司的底色,损失了5000元定金,但帮我排除了以后投资这家公司可能带来的潜在风险。 手机可以堆料,汽车却需要沉淀,不仅仅是技术层面和科技层面的沉淀,更需要安全和可靠性层面的沉淀。 现在我终于想清楚了,小米投资汽车,从战略",
  "title": "小米汽车(BK0888)",
  "url": "https://xueqiu.com/S/BK0888/relevant"
}
{
  "snippet": "小米股价过山车:6000亿说没就没,“一夜”回到造车前 知嘹汽车/费德 小米的股价,又坐了一轮刺激的过山车。进入2026年,当人们把目光投向这家话题不断的公司时,发现其股价已经悄悄跌回了35港元以下,市值徘徊在9000亿港元出头。 这个数字,眼熟得很——差不多就是2024年底小米汽车还没发布时的水平。换句话说,过去一年里一度暴涨的那约6000亿港元市值,已经蒸发得干干净净。资本市场给小米讲的那个宏大造车故事,似乎突然被按下了静音键。 这不仅仅是数字游戏,更直接反映在全球车企的江湖排位上。小米市值曾经一度冲到全球车企第三,风光无限,但现在又被比亚迪反超,跌回了第四。 回看小米市值的狂飙,2023年,小米还是个市值2000多亿港元的手机公司。随着2024年小米SU7(参数|询价|图片)的横空出世,资本市场瞬间沸腾,市值最高冲破了1.5万亿港元。一年多涨了四倍,这哪里是估值?这分明是资本圈为它的跨界造车梦提前预支的溢价。 然而,梦做得太美,醒得也就越突然。2025年站上高峰的小米,最先冒出来的是一堆产品和口碑上的挑战。比如,SU7 Ultra那个听起来很厉害的碳纤维机盖,实际功能和宣传的好像不太一样,甚至为此闹上了法庭。接着,尾灯开裂的品控问题、让车主提前结清尾款的销售政策等等问题,接二连三地冒出来,让不少消费者觉得心里不舒服。 真正砸下重锤的是2026年1月,一天之内连着传出两起涉及小米车辆的事件,虽然公司火速回应说数据正常,但这种消息多了,多少让人心存忧虑。 所以,从1.5万亿高点跌掉将近40%,回到9000亿,这既是市场情绪的退潮,也是投资者在重新审视小米。不过,就算跌了这么多,跟造车前的2000多亿市值比,小米现在还是涨了两倍多。这说明,造车这件事,确实从根本上拔高了大家对它的想象空间。股价的起伏是常态,关键还得看它业务的基本盘能不能接得住这份期待。 核心就看两块——手机和汽车。手机板块,小米在全球市场的基本盘还算稳,冲高端的路子也在继续走。汽车这边,故事则更有戏剧性。2025年第二季度,小米汽车交了8万多台,毛利率还提升到了26%以上;到了第三季度,竟然真的实现了单季盈利。这份成绩单,让一些机构又燃起了希望,甚至预测它2026年销量能翻着跟头涨。",
  "title": "小米股价过山车:6000亿说没就没,“一夜”回到造车前",
  "url": "https://news.m.yiche.com/hao/wenzhang/106995298/"
}
{
  "snippet": "小米汽车(BK0888) 简介:小米汽车",
  "title": "小米汽车(BK0888)",
  "url": "https://www.xueqiu.com/S/BK0888/notices"
}
"""

"""
meta=None content=[TextContent(type='text', text='{"pages":[{"snippet":"描述如何使用万用表来测量电阻。 1. 关闭被测电路电源,确保不带电测量。2. 将万用表旋钮调至电阻测量档(Ω),根据预估阻值选择合适量程(如200Ω、2kΩ等)。3. 将红表笔插入VΩ孔,黑表笔插入COM孔。4. 两表笔金属头分别接触被测电阻两端,保持接触稳定。5. 读取显示屏上的阻值,若显示“OL”则需切换更高量程。6. 测量完成后,将表笔移开并将旋钮调回电压档。 1. **断电安全**:电阻测量需直接接触元件引脚,带电测量易损坏万用表且数据无效。2. **量程选择**:量程过低会导致溢出(显示“OL”),过高则精度下降。数字表可先选自动量程。3. **表笔插孔**:红表笔在VΩ孔确保正确测量电阻,部分表区分开尔文插座需注意特殊场景。4. **接触方式**:双手避免同时触碰表笔金属部分,防止人体电阻并联引入误差。5. **读数判断**:数字表直接显示数值,指针表需观察刻度线(需校零),阻值波动需检查接触或元件故障。6. **复位操作**:防止下次误测高压时因档位错误烧毁仪表(如电阻档误测市电电压)。","hostname":"百度教育","hostlogo":"https://mbs1.bdstatic.com/searchbox/mappconsole/image/20230906/3096e08a-869d-46a7-8d30-5e32adb66fdc.png","title":"描述如何使用万用表来测量电阻。","url":"https://easylearn.baidu.com/edu-page/tiangong/questiondetail?id=1831141635274364705&fr=search"},{"snippet":"一、万用表简介与电阻档的作用 万用表,又称多用表,是电子爱好者和家庭维修者的必备工具。它集电压、电流、电阻等多种测量功能于一身,早在上世纪80年代就已普及,如今已成为五金店和电商平台的热销品。电阻档是万用表的核心档位之一,主要用于测量导体对电流的阻碍程度,即电阻值。电阻值以欧姆(Ω)为单位,反映元件的工作状态:正常电阻应接近标称值,过高可能表示老化,过低则可能短路。 为什么电阻档在家居维修中如此重要?想象一下,家里风扇突然不转了,用电阻档一测电机线圈,就能快速判断是线圈烧坏还是开关故障。相比专业仪器,万用表操作简单、价格亲民,适合家庭的“接地气”维修需求。根据国家标准GB/T 13978-2008,万用表的设计强调安全性和准确性,确保用户在日常使用中不会因误操作而受伤。 电阻档的原理基于欧姆定律:R = U / I(电阻 = 电压 / 电流)。测量时,万用表内部产生一个小电流,通过被测元件,计算出电阻值。这不同于电压档或电流档,电阻档要求电路断电,以避免干扰和安全风险。 二、准备工作:安全第一,工具齐备 在使用电阻档前,做好准备是成功的关键。 1. 首先,确保安全。家庭电器多为220V交流电,测量前必须切断电源,拉下总闸或拔掉插头,避免触电。戴上绝缘手套,工作环境保持干燥通风。 2. 工具方面,需要一个万用表(数字式或指针式均可),红黑表笔,以及被测元件。万用表通常有多个档位:200Ω、2kΩ、20kΩ、200kΩ、2MΩ等,覆盖从毫欧到兆欧范围。数字式显示直观,指针式需手动读表,但两者使用方法相似。用之前,记得检查电池,低电量会影响精度,及时更换9V电池。 3. 准备笔记本记录测量值,并了解元件参数。市场上的电阻器多标有色环码:第一、二环为有效数字,第三环为倍数,第四环为容差。例如,红紫橙金表示2700Ω±5%。通过这些准备,大家能高效启动测量。 三、电阻档的基本操作步骤 万用表电阻档的使用看似简单,实则需注意细节。  步骤1:档位选择 转动旋钮至电阻档(符号为Ω)。根据预计电阻值选档:未知时从高档(如2MΩ)起步,若显示“1”或溢出(OL),切换低档;若接近零,疑为短路。指针式万用表需将表笔短接校零:红","hostname":"百家号","hostlogo":"https://baijiahao.baidu.com/favicon.ico","title":"万用表怎么测电阻?详解万用表电阻档使用方法,电工师傅亲授4大技巧!","url":"https://baijiahao.baidu.com/s?id=1848272339810762338&wfr=spider&for=pc"},{"snippet":"简述用万用表测量电阻的方法。  1. 机械调零:调整机械调零旋钮,使指针静止时指向左侧零位。 2. 选择欧姆档位:将选择开关置于“Ω”区域的合适档位(如R×1k)。 3. 欧姆调零:短接两表笔,调整“Ω”调零旋钮,使指针指向右侧零位;若无法调零,需更换电池。 4. 测量电阻:用表笔接触被测电阻两端,读取指针刻度值并乘以档位倍率。 5. 调整档位:若指针偏转角度过大或过小,更换更高或更低档位并重新调零后再测量。 6. 结束操作:测量完成后,将选择开关置于“OFF”或交流电压最大档位,取出表笔。 注意事项: ① 测量前需将被测电阻从电路中断开; ② 避免表笔长时间短接或双手同时接触金属部分; ③ 长期不用时应取出表内电池。","hostname":"百度教育","hostlogo":"https://mbs1.bdstatic.com/searchbox/mappconsole/image/20230906/3096e08a-869d-46a7-8d30-5e32adb66fdc.png","title":"简述用万用表测量电阻的方法。 ","url":"https://easylearn.baidu.com/edu-page/tiangong/questiondetail?id=1813578077628852876&fr=search"}],"request_id":"e053b80b-0446-91b6-9fda-b28818f32f88","tools":[],"status":0}', annotations=None, meta=None)] structuredContent=None isError=False
{
  "snippet": "描述如何使用万用表来测量电阻。 1. 关闭被测电路电源,确保不带电测量。2. 将万用表旋钮调至电阻测量档(Ω),根据预估阻值选择合适量程(如200Ω、2kΩ等)。3. 将红表笔插入VΩ孔,黑表笔插入COM孔。4. 两表笔金属头分别接触被测电阻两端,保持接触稳定。5. 读取显示屏上的阻值,若显示“OL”则需切换更高量程。6. 测量完成后,将表笔移开并将旋钮调回电压档。 1. **断电安全**:电阻测量需直接接触元件引脚,带电测量易损坏万用表且数据无效。2. **量程选择**:量程过低会导致溢出(显示“OL”),过高则精度下降。数字表可先选自动量程。3. **表笔插孔**:红表笔在VΩ孔确保正确测量电阻,部分表区分开尔文插座需注意特殊场景。4. **接触方式**:双手避免同时触碰表笔金属部分,防止人体电阻并联引入误差。5. **读数判断**:数字表直接显示数值,指针表需观察刻度线(需校零),阻值波动需检查接触或元件故障。6. **复位操作**:防止下次误测高压时因档位错误烧毁仪表(如电阻档误测市电电压)。",
  "title": "描述如何使用万用表来测量电阻。",
  "url": "https://easylearn.baidu.com/edu-page/tiangong/questiondetail?id=1831141635274364705&fr=search"
}
{
  "snippet": "一、万用表简介与电阻档的作用 万用表,又称多用表,是电子爱好者和家庭维修者的必备工具。它集电压、电流、电阻等多种测量功能于一身,早在上世纪80年代就已普及,如今已成为五金店和电商平台的热销品。电阻档是万用表的核心档位之一,主要用于测量导体对电流的阻碍程度,即电阻值。电阻值以欧姆(Ω)为单位,反映元件的工作状态:正常电阻应接近标称值,过高可能表示老化,过低则可能短路。 为什么电阻档在家居维修中如此重要?想象一下,家里风扇突然不转了,用电阻档一测电机线圈,就能快速判断是线圈烧坏还是开关故障。相比专业仪器,万用表操作简单、价格亲民,适合家庭的“接地气”维修需求。根据国家标准GB/T 13978-2008,万用表的设计强调安全性和准确性,确保用户在日常使用中不会因误操作而受伤。 电阻档的原理基于欧姆定律:R = U / I(电阻 = 电压 / 电流)。测量时,万用表内部产生一个小电流,通过被测元件,计算出电阻值。这不同于电压档或电流档,电阻档要求电路断电,以避免干扰和安全风险。 二、准备工作:安全第一,工具齐备 在使用电阻档前,做好准备是成功的关键。 1. 首先,确保安全。家庭电器多为220V交流电,测量前必须切断电源,拉下总闸或拔掉插头,避免触电。戴上绝缘手套,工作环境保持干燥通风。 2. 工具方面,需要一个万用表(数字式或指针式均可),红黑表笔,以及被测元件。万用表通常有多个档位:200Ω、2kΩ、20kΩ、200kΩ、2MΩ等,覆盖从毫欧到兆欧范围。数字式显示直观,指针式需手动读表,但两者使用方法相似。用之前,记得检查电池,低电量会影响精度,及时更换9V电池。 3. 准备笔记本记录测量值,并了解元件参数。市场上的电阻器多标有色环码:第一、二环为有效数字,第三环为倍数,第四环为容差。例如,红紫橙金表示2700Ω±5%。通过这些准备,大家能高效启动测量。 三、电阻档的基本操作步骤 万用表电阻档的使用看似简单,实则需注意细节。  步骤1:档位选择 转动旋钮至电阻档(符号为Ω)。根据预计电阻值选档:未知时从高档(如2MΩ)起步,若显示“1”或溢出(OL),切换低档;若接近零,疑为短路。指针式万用表需将表笔短接校零:红",
  "title": "万用表怎么测电阻?详解万用表电阻档使用方法,电工师傅亲授4大技巧!",
  "url": "https://baijiahao.baidu.com/s?id=1848272339810762338&wfr=spider&for=pc"
}
{
  "snippet": "简述用万用表测量电阻的方法。  1. 机械调零:调整机械调零旋钮,使指针静止时指向左侧零位。 2. 选择欧姆档位:将选择开关置于“Ω”区域的合适档位(如R×1k)。 3. 欧姆调零:短接两表笔,调整“Ω”调零旋钮,使指针指向右侧零位;若无法调零,需更换电池。 4. 测量电阻:用表笔接触被测电阻两端,读取指针刻度值并乘以档位倍率。 5. 调整档位:若指针偏转角度过大或过小,更换更高或更低档位并重新调零后再测量。 6. 结束操作:测量完成后,将选择开关置于“OFF”或交流电压最大档位,取出表笔。 注意事项: ① 测量前需将被测电阻从电路中断开; ② 避免表笔长时间短接或双手同时接触金属部分; ③ 长期不用时应取出表内电池。",
  "title": "简述用万用表测量电阻的方法。 ",
  "url": "https://easylearn.baidu.com/edu-page/tiangong/questiondetail?id=1813578077628852876&fr=search"
}
"""
