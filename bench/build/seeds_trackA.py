# Builds ReadProof-Bench Track A seed list from web-search results recorded on 2026-11-21.
# Every url and title below is copied verbatim from the WebSearch result list
# (title field of the result), never from the tool's prose summary.
import json, re, sys, collections
from urllib.parse import urlparse, parse_qs, unquote

OUT = sys.argv[1]
FOUND_AT = "2026-11-21"
ENGINE = "claude-websearch"

# (platform, url, title, domain, language, query, allowed_domains)
R = []
def add(platform, url, title, domain, language, query, allowed):
    R.append(dict(platform=platform, url=url, title=title, domain=domain,
                  language=language, query=query, allowed=allowed))

DY = ["douyin.com"]
# ---------------- douyin ----------------
q = "抖音 视频 家常菜 教程 douyin.com/video"
add("douyin", "https://www.douyin.com/video/7506900431965359394", "这才是家常煎豆腐的正确做法，外焦里嫩又入味，真的超级好吃 #煎豆腐 #家常豆腐 #家常菜教程 #简单家常菜 #抖音美食推荐官 @抖音小助手 @抖音美食达人助手 @抖音创作小助手 - 抖音", "food_recipes", "zh", q, DY)
add("douyin", "https://www.douyin.com/video/7572834845853829989", "#家常菜 #家常菜教程 #教你做美食 - 抖音", "food_recipes", "zh", q, DY)
q = "旅行攻略 收藏 #旅行推荐官 抖音 video"
add("douyin", "https://www.douyin.com/video/7622210467506834703", "6 天玩透昆大丽，4 月的云南每一帧都像壁纸 #旅行推荐官 #喜洲麦田 #风景如画的地方 #海景酒店 #旅行攻略 - 抖音", "travel", "zh", q, DY)
q = "自驾游 景点 推荐 #旅行 抖音"
add("douyin", "https://www.douyin.com/video/7619159016261897482", "超全铁贾公路&喀纳斯秘境自驾攻略！收藏好。这条藏在阿勒泰的 - 抖音", "travel", "zh", q, DY)
q = "手机测评 抖音 视频"
add("douyin", "https://www.douyin.com/video/7597416406057280753", "一镜到底！四台机器的实测成绩告诉你华为到底是不是4G #手机 #华为 #vivo #4G #5G - 抖音", "consumer_tech_ai", "zh", q, DY)
q = "AI工具 推荐 #人工智能 抖音 video"
add("douyin", "https://m.douyin.com/share/video/7457443186969972006", "利用AI工具制作轻松制作2025年新年精美视频#aimate #ai工具 #人工智能 #迎财神 @DOU+小助手 @抖加🔥上热门🔥dou+🔥热点宝 - 抖音", "consumer_tech_ai", "zh", q, DY)
q = "化妆教程 新手 #美妆 抖音 video"
add("douyin", "https://www.douyin.com/video/7558411756868914482", "适合新手 零基础 不会化妆的日常百搭淡妆 步骤简单 超详细全妆跟练 赶紧练起来吧~#普通人化妆 #新手化妆教程 #妆容教程 #方圆脸 #化妆分享 @DOU+上热门 @抖音小助手 - 抖音", "beauty_skincare", "zh", q, DY)
q = "护肤 干皮 精华 抖音 视频"
add("douyin", "https://www.douyin.com/video/7687246950319525156", "球球同款厚脸皮面霜，缓解肌肤小情绪#厚脸皮面霜 #面霜 #敏肌护肤 #沙漠大干皮 - 抖音", "beauty_skincare", "zh", q, DY)
q = "育儿知识 宝宝 抖音 视频"
add("douyin", "https://www.douyin.com/video/7687632198905036969", "宝宝月子里您必须知道的五件事#育儿经验分享 #母婴知识科普 #新手爸妈必看指南 #新手爸妈日常记录 #母婴知识科普 - 抖音", "parenting", "zh", q, DY)
add("douyin", "https://www.douyin.com/video/6808084783118175495", "一秒止哭的飞机抱，缓解宝宝肠绞痛，肠胀气！#新手爸妈 #母婴 #时尚潮人鉴定局 @抖音小助手 - 抖音", "parenting", "zh", q, DY)
q = "新能源汽车 试驾 #汽车 抖音"
add("douyin", "https://www.douyin.com/video/7537619385570430265", "都说合资车不行了？！ 但是丰田铂智3X似乎让我们看到了一些有意思的设计。 #丰田铂智3X #新能源汽车 - 抖音", "cars_evs", "zh", q, DY)
add("douyin", "https://www.douyin.com/video/7122346336204197134", "vlog带你来看深圳比亚迪！总算肝出来了。#汉ev #试驾#vlog日常 #比亚迪 #汽车 #参观 #企业 - 抖音", "cars_evs", "zh", q, DY)
q = "财经 解读 经济 #财经 #金融 知识 - 抖音"
add("douyin", "https://www.douyin.com/video/7473782091507944744", "突破了，节奏很稳健 #财经 #股票 #金融 - 抖音", "finance_news", "zh", q, DY)
q = "短剧 #短剧推荐 抖音"
add("douyin", "https://www.douyin.com/video/7610438284593859867", "#短剧推荐# 免费短剧全集，一口气看完，上班族必备，精彩到停不下来！ - 抖音", "entertainment_drama", "zh", q, DY)
add("douyin", "https://www.douyin.com/video/7699053575086918966", "2026年火爆全网的短剧，剧情惊艳口碑炸裂，你还没看？ - 抖音", "entertainment_drama", "zh", q, DY)

# ---------------- xiaohongshu / rednote ----------------
XH = ["xiaohongshu.com"]
XR = ["xiaohongshu.com", "rednote.com"]
RN = ["rednote.com"]
add("xiaohongshu", "https://www.xiaohongshu.com/explore/6a12e78c000000000803dbc9", "护肤真的可以简单！坚持坚持坚持 - 小红书", "beauty_skincare", "zh", "护肤 小红书 笔记", XH)
add("xiaohongshu", "https://www.rednote.com/en/discovery/item/69ccec66000000002202a360", "SkinCare｜30+ My Long-Term Nighttime Skincare Ritual🧴 - rednote", "beauty_skincare", "en", "rednote skincare routine discovery item", RN)
add("xiaohongshu", "https://www.xiaohongshu.com/explore/6a3f5d000000000011007133", "简单家常菜做法+选材+火候要点=超下饭‼️ - 小红书", "food_recipes", "zh", "家常菜 做法 食谱 小红书", XR)
add("xiaohongshu", "https://www.rednote.com/discovery/item/6a01fc5700000000360037cd", "🔥一周不重样！9道家常菜直接抄作业 - rednote", "food_recipes", "zh", "rednote Chinese food recipe note", RN)
add("xiaohongshu", "https://www.xiaohongshu.com/explore/69d8749b000000001f003ee2", "重庆三天两晚攻略！火锅与晚风都是浪漫❤️‍🔥 - 小红书", "travel", "zh", "旅行攻略 三天两晚 小红书", XR)
add("xiaohongshu", "https://www.rednote.com/en/discovery/item/69e6dd2200000000230233b8", "✈My Singapore travel checklist📝 - rednote", "travel", "en", "rednote explore note travel", RN)
add("xiaohongshu", "https://www.rednote.com/en/discovery/item/69a03d5a000000001a020817", "So handy! rednote AI can now help you plan your travel itinerary! - rednote", "consumer_tech_ai", "en", "rednote explore note travel", RN)
q = "AI 工具 效率 推荐 小红书 笔记"
add("xiaohongshu", "https://www.xiaohongshu.com/explore/6a9fbf0a000000002601b910", "2026AI工具分类表｜打工人提高效率必备🤖 - 小红书", "consumer_tech_ai", "zh", q, XR)
add("xiaohongshu", "https://www.xiaohongshu.com/explore/6a50bd27000000000f031fdd", "AI 搜索工具横向测评｜选对少走弯路✨ - 小红书", "consumer_tech_ai", "zh", q, XR)
q = "育儿 经验 分享 新手妈妈 小红书"
add("xiaohongshu", "https://www.xiaohongshu.com/explore/6a461d3e0000000006033ac5", "带娃听谁的？有效建议！📢 - 小红书", "parenting", "zh", q, XR)
add("xiaohongshu", "https://www.xiaohongshu.com/explore/6a2262f40000000038020e9a", "育儿难题，书里能找到答案？ - 小红书", "parenting", "zh", q, XR)
add("xiaohongshu", "https://www.xiaohongshu.com/explore/6a50edb2000000001100600d", "比亚迪大唐提车3天真实测评 - 小红书", "cars_evs", "zh", "新能源车 提车 用车 感受 小红书", XR)
add("xiaohongshu", "https://www.xiaohongshu.com/explore/69d8afb2000000002300432d", "手把手教你设置特斯拉主动安全✨新手必看 - 小红书", "cars_evs", "zh", "特斯拉 Model Y 提车 一个月 感受 小红书", XR)
q = "存钱 理财 攒钱 记账 小红书"
add("xiaohongshu", "https://www.xiaohongshu.com/explore/694df43c000000001e0222e5", "35岁普通家庭︱一张A4纸盘2026年存钱计划 - 小红书", "finance_news", "zh", q, XR)
add("xiaohongshu", "https://www.xiaohongshu.com/explore/6aa00cbb000000002601b6b1", "12大存钱法，越存越上瘾🔥（必看） - 小红书", "finance_news", "zh", q, XR)
q = "短剧 推荐 好看 小红书 笔记"
add("xiaohongshu", "https://www.xiaohongshu.com/explore/6951c5de000000001e01036f", "短剧推荐：别怕，我比你还疯 推荐10星 - 小红书", "entertainment_drama", "zh", q, XR)
add("xiaohongshu", "https://www.xiaohongshu.com/explore/68e50948000000000503b3fe", "国庆放假7天看的短剧合集 - 小红书", "entertainment_drama", "zh", q, XR)

# ---------------- bilibili ----------------
BL = ["bilibili.com"]
q = "家常菜 做法 美食 教程 视频"
add("bilibili", "https://www.bilibili.com/video/BV13Axv6EEjU/", "学会这6道家常菜，你就是厨房的高手了。羊肚菌虾滑炒芦笋、山药紫薯饼、腐竹三鲜煲、土豆芝士饼、番茄炒花菜、土豆泥拌面_哔哩哔哩_bilibili", "food_recipes", "zh", q, BL)
add("bilibili", "https://www.bilibili.com/video/BV1BeJC6qEuP/", "厨师长分享：“干炒牛河”的家常做法，干香嫩滑，锅气十足_哔哩哔哩_bilibili", "food_recipes", "zh", q, BL)
q = "云南 大理 旅游 攻略 自由行 视频"
add("bilibili", "https://www.bilibili.com/video/BV1pEQZYqEBc/", "云南旅游3天2晚自由行攻略整理_哔哩哔哩_bilibili", "travel", "zh", q, BL)
add("bilibili", "https://www.bilibili.com/video/BV1vcWm6nEqG/", "看看有熟悉的吗？2026年11月新增的17家5A景区_哔哩哔哩_bilibili", "travel", "zh", q, BL)
add("bilibili", "https://www.bilibili.com/video/BV1xZ4n6ME4c/", "手机又准备涨价了！2026年11月手机推荐_哔哩哔哩_bilibili", "consumer_tech_ai", "zh", "手机 测评 视频 2026", BL)
add("bilibili", "https://www.bilibili.com/video/BV14qNg6xEDP/", "月耗万刀实测｜2026年7月｜21款旗舰AI模型｜多维度横向测评_哔哩哔哩_bilibili", "consumer_tech_ai", "zh", "AI 大模型 实测 对比 视频 2026", ["bilibili.com", "b23.tv", "m.bilibili.com"])
q = "护肤 干货 美妆 视频 教程"
add("bilibili", "https://www.bilibili.com/video/BV1khcozHE9f/", "【全网最全】早晚护肤全流程 全程无广不推荐任何产品_哔哩哔哩_bilibili", "beauty_skincare", "zh", q, BL)
add("bilibili", "https://www.bilibili.com/video/av19891708/", "护肤整体流程，超全，赞一个_哔哩哔哩_bilibili", "beauty_skincare", "zh", q, BL)
q = "育儿 科普 新手爸妈 视频"
add("bilibili", "https://www.bilibili.com/video/BV1ugCdYdESr/", "宝宝卸货前，新手奶爸必学的9项技能，准爸爸必看❗❗快点收藏自查一下哈～#准爸爸#新手爸妈#新生儿护理#科学坐月子#母婴日常_哔哩哔哩_bilibili", "parenting", "zh", q, BL)
add("bilibili", "https://www.bilibili.com/video/BV1Ti4y1X7rd/", "最高豆瓣9.8分！这4本书，妈妈们应该人手一本！ 如果你也有育儿焦虑。。。_哔哩哔哩_bilibili", "parenting", "zh", q, BL)
q = "新能源 电车 评测 试驾 视频"
add("bilibili", "https://www.bilibili.com/video/BV1x2Y2zVEeG/", "详细体验全新蔚来ES8_哔哩哔哩_bilibili", "cars_evs", "zh", q, BL)
add("bilibili", "https://www.bilibili.com/video/BV1nNHqeDEej/?spm_id_from=333.788.recommend_more_video.8", "【沉浸式试车】零跑T03五门四座纯电车，赶紧沉浸式体验_哔哩哔哩_bilibili", "cars_evs", "zh", q, BL)
q = "理财 基金 经济 解读 视频"
add("bilibili", "https://www.bilibili.com/video/BV1zLxM6sEWu/", "30万如何系统理财？普通人拉开财富差距的分水岭！_哔哩哔哩_bilibili", "finance_news", "zh", q, BL)
add("bilibili", "https://www.bilibili.com/video/BV1EU4y1F7XY/", "韩秀云｜追涨杀跌？！靠基金赚钱，首先你要搞懂这些逻辑！_哔哩哔哩_bilibili", "finance_news", "zh", q, BL)
add("bilibili", "https://www.bilibili.com/video/BV15Dp16LESn/", "2026最后一波热门待播剧盘点，你押宝哪一部？_哔哩哔哩_bilibili", "entertainment_drama", "zh", "2026 好看的电视剧 综艺 推荐 盘点 视频", BL)
add("bilibili", "https://www.bilibili.com/video/BV1YuDqYFEYp/", "短剧最终会发展成一个什么样的产业？_哔哩哔哩_bilibili", "entertainment_drama", "zh", "短剧 解说 综艺 电视剧 推荐 视频", BL)

# ---------------- youtube ----------------
YT = ["youtube.com"]
add("youtube", "https://www.youtube.com/watch?v=OTAwvdRJn6M", "5 Easy Dinners You Can Make in 30 Minutes or Less (Garlic Butter Chicken Bites & More)", "food_recipes", "en", "easy dinner recipe", YT)
add("youtube", "https://www.youtube.com/shorts/rL5QEE27Si0", "Easy 5-Minute Breakfast", "food_recipes", "en", "youtube shorts recipe #shorts", YT)
add("youtube", "https://www.youtube.com/watch?v=C_XHkTu-raw", "教你8種超級下飯的家常菜，做法簡單，學會了飯桌上天天不重樣【小家廚記】 - YouTube", "food_recipes", "zh", "家常菜 做法 中文 教學", YT)
q = "Japan travel guide first time itinerary"
add("youtube", "https://www.youtube.com/watch?v=WOV8RkQyzU0", "7 Days in Japan — The Ultimate First-Timer's Guide (Tokyo, Kyoto, Osaka, Hakone) - YouTube", "travel", "en", q, YT)
add("youtube", "https://www.youtube.com/watch?v=4Mtc2E7fkZ4&pp=ygUMI2l0ZW5hcnlwbGFu", "Japan Travel Planning Made Easy: 10-Day Itinerary for First-Timers - YouTube", "travel", "en", q, YT)
add("youtube", "https://www.youtube.com/watch?v=xrFVF1SuCDc", "iPhone 18 Pro Camera Test - Pushed to the LIMITS! - YouTube", "consumer_tech_ai", "en", "iPhone review camera test 2026", YT)
add("youtube", "https://www.youtube.com/watch?v=aKuAyll41a8", "Every New AI Model Explained (2026) - YouTube", "consumer_tech_ai", "en", "AI agents explained 2026 new model review", YT)
q = "skincare routine dermatologist explains"
add("youtube", "https://www.youtube.com/watch?v=u2l8H5qjK7A", "Skinimalism: The Dermatologist-Approved Minimal Skincare Routine - YouTube", "beauty_skincare", "en", q, YT)
add("youtube", "https://www.youtube.com/watch?v=4SxAWpr97Yg&xstg=CAMSBhUDzO3xHw%3D%3D", "The ONLY Skincare Routine Men Actually Need", "beauty_skincare", "en", q, YT)
q = "parenting tips toddler tantrums"
add("youtube", "https://www.youtube.com/watch?v=uJnQ5NLKn_c", "How to Handle Toddler Tantrums: Pediatrician Explains the S.E.R.E.N.E. Method - YouTube", "parenting", "en", q, YT)
add("youtube", "https://www.youtube.com/embed/GBvQvi79Fw4", "10 Parenting Tips to Calm Down Any Child In a Minute - YouTube", "parenting", "en", q, YT)
add("youtube", "https://www.youtube.com/watch?v=tp3L-yI19ds", "100,000 Miles Later Is the Mach-E Still Awesome? My Long Term Ownership Review - YouTube", "cars_evs", "en", "EV review long term ownership electric car", YT)
add("youtube", "https://www.youtube.com/watch?v=cz_hOk1CiVw&xstg=CAMSBhUD_LL2Hw%3D%3D", "【集評】4款電動休旅車 同臺實測〔200萬元級距〕iX1、Ioniq 5、Model Y、EX30｜長里程能耗／充電速度／駕駛輔助／空間配備｜U-CAR 捉對集評 - YouTube", "cars_evs", "zh", "電動車 評測 試駕 中文", YT)
add("youtube", "https://www.youtube.com/watch?v=OdYxJBuglhs", "How to Save and Budget as a Beginner (2026 Money Reset) - YouTube", "finance_news", "en", "personal finance how to budget save money explained", YT)
add("youtube", "https://www.youtube.com/watch?v=FImxUf0jKGI", "Gatto - Official Trailer (2026) Mark Ruffalo, Laurence Fishburne - YouTube", "entertainment_drama", "en", "movie trailer official 2026", YT)
add("youtube", "https://www.youtube.com/watch?v=YlXpkMcY78I", "【2026最新】一度見たら朝。人生狂わす中国ショートドラマ《短劇》10選 - YouTube", "entertainment_drama", "ja", "短劇 推薦 2026 中文 合集", YT)

# ---------------- tiktok ----------------
TT = ["tiktok.com"]
add("tiktok", "https://www.tiktok.com/@mostasting/video/7264349696917769514?lang=en", "Delicious Home Cooking Dinner Recipes, where my Food Lovers at ? #food #cooking #recipe #dinner #cookingtiktok #cook #recipes #recipesoftiktok", "food_recipes", "en", "recipe video", TT)
add("tiktok", "https://www.tiktok.com/@china_taste001/video/7559379969516915988", "手把手教你大闸蟹的正确吃法，步骤详细，吃法简单。#美食教程 #美食 #fyp #美食分享 #tiktokfood #大闸蟹 #螃蟹", "food_recipes", "zh", "美食 做法 教程 抖音 tiktok 中文 video", TT)
q = "travel tips itinerary video tiktok"
add("tiktok", "https://www.tiktok.com/@rileejsmith/video/7285135399062670634?lang=en", "My tips for planning the ultimate travel itinerary! ✈️⭐️ The travel planning app I used is called Wanderlog and i’m obsessed 🙌🏼 #traveltips #travelplanner #travelitinerary #travelplanning #traveltiktok #travellife #wanderlog #travel", "travel", "en", q, TT)
add("tiktok", "https://www.tiktok.com/@tiktoktravel50/video/7324659353733123329", "中国张家界5天4晚旅游攻略#中国旅游 #中国旅行 #travel #张家界旅游攻略 #中国张家界 #中国旅游景点", "travel", "zh", "张家界 旅游攻略 视频 tiktok @ video", TT)
add("tiktok", "https://www.tiktok.com/@smartworkai/video/7377149970253712683", "How to use ChatGPT to make a travel itinerary for your next vacation. #chatgptprompts #chatgpthack #chatgpt #travelplanning #vacationplanning #traveltiktok", "consumer_tech_ai", "en", q, TT)
add("tiktok", "https://www.tiktok.com/@techcityexplorer/video/7595529018091343112", "Unboxing the iPhone 17... yes, the BASE model. Come fight me. Before you ask.. no, I didn't get the Pro. Yes, I know it looks like the 16. No, I don't care. I just saved over ₦250,000 and got ProMotion 120Hz, A19 chip, and great cameras. Pro iPhone buyers, how's that titanium frame treating you? Oh wait, also aluminum? and still dents? Interesting. #iPhone17 #Unboxing #BaseiPhone #SmartBuying #iPhoneUnboxing", "consumer_tech_ai", "en", "phone review unboxing tech tiktok video", TT)
q = "skincare routine dermatologist tiktok"
add("tiktok", "https://www.tiktok.com/@dr.tomassian/video/7279565705207434539", "Here is your personal dermatologist approved skincare routine! #drtomassian #dermatologist #skincareroutine", "beauty_skincare", "en", q, TT)
add("tiktok", "https://www.tiktok.com/@teawithmd/video/7541122802112417054", "Let me build you a SIMPLE bare bones #skincareroutine: 🌞 AM: 🧼 Cleanse (optional) → 🍊 Vitamin C → ☀️ SPF 🌙 PM: 🧼 Cleanse → ✨ Retinoid → 💧 Moisturizer Simple. Effective. Dermatologist-approved ✅ No Scrubs here! #millennialsoftiktok #millennial #skincaretips", "beauty_skincare", "en", q, TT)
q = "parenting tips toddler video tiktok"
add("tiktok", "https://www.tiktok.com/@peds_slp/video/7411231966063856926", "Parenting tips to help baby start talking!   #parents #parentsoftiktok #parenting #parentingtips #parent #parentsbelike #parenthood #trashyparenting #gentleparenting #parentinghacks #parenting101 #kids #children #creatorsearchinsights", "parenting", "en", q, TT)
add("tiktok", "https://www.tiktok.com/@blippi/video/7523212467208539447?lang=en", "Blippi - Navigating Toddler Parenting: Tips and Humor", "parenting", "en", q, TT)
q = "EV review electric car test drive tiktok"
add("tiktok", "https://www.tiktok.com/@ryan_nyc/video/7425818475253353774", "Test drove 10 electric cars at @Electrify Expo #carreview #ev #5star", "cars_evs", "en", q, TT)
add("tiktok", "https://www.tiktok.com/@tim.stevens/video/7470171462477991214", "More questions about the second-gen @rivian R1S. This one's about the cargo space out back. Stay tuned for a look at that third row! Check out the full video over on YouTube! https://youtu.be/skoNLz9rI-w?si=lVpBF4xJ50YNxYpS #ev #rivian #rivianr1s #carreview #review", "cars_evs", "en", q, TT)
q = "budgeting tips save money personal finance video tiktok"
add("tiktok", "https://www.tiktok.com/@yourrichbff/video/7469092038613486894", "Looking for a budgeting plan that will ACTUALLY work?  #money #finance #financialfreedom #learn #lifehack #save #savemoney #moneymaker #budget #savings #plan #banking", "finance_news", "en", q, TT)
add("tiktok", "https://www.tiktok.com/@thebudgetingprincess/video/7432882506145238314", "Effective Budgeting Tips: Save $10,500 in 9 Months", "finance_news", "en", q, TT)
add("tiktok", "https://www.tiktok.com/@heymattlab/video/7628270370499431693", "New Sunrise on the Reaping trailer reaction!!! #fyp #reaction #review #movietok #hungergames", "entertainment_drama", "en", "movie review trailer reaction video tiktok", TT)
add("tiktok", "https://www.tiktok.com/@qupbiiqjr8/video/7614479329715555605", "《暗途猎金》《青鳞夺运》《独门笔记》#短剧#短剧推荐 #红果短剧", "entertainment_drama", "zh", "短剧 推荐 中文 tiktok video #短剧", TT)

# ---------------- x ----------------
XX = ["x.com", "twitter.com"]
q = "recipe dinner idea post status"
add("x", "https://x.com/Allrecipes/status/2085335136774590795", 'Allrecipes on X: "It\'s easy, nutritious, and delicious. https://t.co/dW13ZlXo6V" / X', "food_recipes", "en", q, XX)
add("x", "https://x.com/coookwithchris/status/2102081418264183205", 'Cooking with Chris on X: "Another go-to meal when I have almost zero time to cook 3 ingredients: chicken, enchilada sauce and salt Pressure cooked for 10 minutes and shred it up 300g of protein total" / X', "food_recipes", "en", q, XX)
add("x", "https://x.com/TravelGov/status/1965051094616146404", 'TravelGov on X: "Don’t let this common mistake ruin your vacation! Many countries require your passport to be valid for at least SIX MONTHS beyond your travel dates. Always check the passport validity requirements for your destination at https://t.co/JeUUxsLC0G to avoid getting turned around at https://t.co/kD5UYuCxFN" / X', "travel", "en", "travel destination tips status post", XX)
add("x", "https://x.com/roundtripJp/status/1868857022814568750", '巡日旅行攝 on X: "2025年日本各地梅花開花預測與賞梅花指南! #巡日 #日本 #旅行 #二零二五 #出國 #梅花 #梅花預測 #賞梅情報 #僅供參考 #日本自由行 #巡日指南 #巡日旅行攝 #Japan #Travel #2O25 #Plumblossom #梅の花 #うめ #ウメ #Guide #Tips #RoundtripJp https://t.co/3gD4QioWeV" / X', "travel", "zh", "旅行 攻略 日本 中文 推文 status", XX)
add("x", "https://x.com/AirChinaJP/status/1972943396160549077", 'Air China Japan on X: "美味しい料理を口にしたとき、中国語では「好吃（ハオチー）」と言います。 これは日本語の「美味しい」と同じ意味であり、料理への評価であると同時に、 料理人への敬意や感謝の気持ちも込められています。 次に中国を訪れた際、美味しいものに出会ったときは、ぜひ「好吃！」と言ってみてください。" / X', "travel", "ja", "美食 推荐 中文 推文 status", XX)
add("x", "https://x.com/OpenAI/status/2086864365379010729", 'OpenAI on X: "We’re expanding our cybersecurity initiative Daybreak and introducing GPT-5.6-Cyber, a new model for advanced, authorized cybersecurity work. As the threat landscape evolves, we’re putting frontier intelligence in the hands of trusted defenders before attackers can deploy offensive A… / X', "consumer_tech_ai", "en", "OpenAI announces new model post status", XX)
add("x", "https://x.com/berryxia_ai/status/1992989895850430908", 'Berryxia.AI on X: "来啊！ 我们让他直接可以让路边摊大排档也有五星级高级感动海报啊！ 我将提示词迭代了一下，使其更符直白名了！ Prompt： At the very top center of the composition, floating prominently above the ingredient layers, is a luxurious title label. The text reads "担担面 DAN DAN NOODLES" https://t.co/pc0Jvshtm9" / X', "consumer_tech_ai", "zh", "美食 推荐 中文 推文 status", XX)
q = "skincare sunscreen tip status post"
add("x", "https://x.com/SkinCareBully/status/2084115142560579844", 'Skin Care Bully on X: "Cleanse. Treat. Hydrate. Moisturize. Use sunscreen every morning." / X', "beauty_skincare", "en", q, XX)
add("x", "https://x.com/ClevelandClinic/status/1930211611307810861", 'Cleveland Clinic on X: "Protect your skin from harmful UV rays by using both UPF clothing and sunscreen. UPF clothing provides great coverage, but don\'t forget to apply sunscreen to exposed areas like your hands, neck and face. https://t.co/nyromFMHPv" / X', "beauty_skincare", "en", q, XX)
q = "parenting advice tip status post"
add("x", "https://x.com/vanderjames/status/1381305023297167361", 'James Van Der Beek on X: "Best early parenting advice I ever got was: Accept that there’s no such thing as a perfect parent. We’re all doing the best we can in the moment - and usually on less sleep than we need. Recognize, learn, forgive yourself, and keep loving your kid with a heart free of guilt." / X', "parenting", "en", q, XX)
add("x", "https://twitter.com/DaddingAround/status/1116312421709352960", 'Dadding Around on X: "Helpful parenting tip No.542: Never allow your child into your bed with any item that you wouldn\'t like to be hit in the head with repeatedly. #parenting #dadlife" / X', "parenting", "en", q, XX)
add("x", "https://x.com/TheCaptainEli/status/2121189766360117574", 'Captain Eli on X: "Every Model Y Premium trim in the US is sold out for 2026. A new order gets quoted January to March 2027, and the Model Y L is quoted February to April. That\'s more than a year after the US federal EV tax credit disappeared, for a car that has been on sale since 2020. Back in 2… / X', "cars_evs", "en", "Tesla Model Y status post", XX)
add("x", "https://x.com/DailyBeijing/status/2074749612133589425", 'Beijing Daily on X: "小米将发布首款增程车，命名“澎程”。 Xiaomi will launch its first range-extender electric vehicle, named "SkyNomad." https://t.co/bu1QX4U1MW" / X', "cars_evs", "zh", "小米汽车 新车 发布 推文 status", XX)
q = "personal finance tip saving investing status post"
add("x", "https://x.com/NerdWallet/status/1882147379656507827", 'NerdWallet on X: "Tips to make your money-saving resolutions stick." / X', "finance_news", "en", q, XX)
add("x", "https://twitter.com/Stash/status/1293219695965995009?lang=en", 'Stash on X: "✅ Start a budget Track how much money you make and spend. You can use a spreadsheet, a pen and paper, the notes app on your phone, or a dedicated budgeting app. But do it for at least a month. At the end you’ll know, roughly, what your income and expenses are per month." / X', "finance_news", "en", q, XX)
q = "Netflix new series trailer announcement status"
add("x", "https://x.com/netflix/status/2121317172379033635", 'Netflix on X: "Every great story begins with a spark. Here’s your first look at PLANESWALKER, a new animated series based on Magic: The Gathering. Coming to Netflix in 2027." / X', "entertainment_drama", "en", q, XX)
add("x", "https://x.com/DiscussingFilm/status/2115814910726922567", 'DiscussingFilm on X: "First trailer for Philomena Cunk\'s new series ‘CUNK ON CINEMA’. The series follows Cunk as she attempts to discover why movies exist and what they want from us. Releasing November 15 on Netflix." / X', "entertainment_drama", "en", q, XX)

# ---------------- weibo ----------------
WB = ["weibo.com"]
WBM = ["weibo.com", "m.weibo.cn"]
add("weibo", "https://weibo.com/2/detail/5344861387493267", "穿梭城市搜罗市井特色美食 实拍制作邀体验舌尖快乐", "food_recipes", "zh", "美食 探店 好吃 微博 detail 博主", WB)
add("weibo", "https://weibo.com/2/detail/5353573301094921", "烟火里的治愈美食：与好友共度的温暖食光", "food_recipes", "zh", "美食", ["m.weibo.cn", "weibo.com"])
add("weibo", "https://weibo.com/2/detail/5350602851750379", "假期选择周边游 感受自然松弛感", "travel", "zh", "景区 旅游 出游 假期 微博 detail", WB)
add("weibo", "https://weibo.com/2/detail/comos:nizmwas9943793", "4天毕业旅行路线怎么选？3条实测路线+分人群闭眼入+FAQ", "travel", "zh", "旅行 攻略 微博正文", WBM)
q = "AI 大模型 发布 科技 微博"
add("weibo", "https://weibo.com/2/detail/5346259729909871", "2026云栖大会：阿里Qwen系列大模型及视频生成模型最新进展", "consumer_tech_ai", "zh", q, WBM)
add("weibo", "https://weibo.com/2/detail/5361878491988467", "OpenAI GPTImages 2.6上线：支持GPT直接生成GIF 生图提速50%", "consumer_tech_ai", "zh", q, WBM)
add("weibo", "https://weibo.com/2/detail/5367285284079865", "小鹏2026科技日11月25日广州举办 发布图灵AI共驾", "cars_evs", "zh", q, WBM)
add("weibo", "https://weibo.com/ttarticle/x/m/geoshow/id/2309405366244819206168?domain=auto", "中国电车“下南洋”：上半场卖车，下半场建网", "cars_evs", "zh", "新能源 汽车 观察 文章", WBM)
add("weibo", "https://weibo.com/2/detail/5365154603862782", "秋冬仍需防晒 薇诺娜清透防晒乳修屏障兼防晒", "beauty_skincare", "zh", "护肤 防晒 美妆 新品 微博 detail", WBM)
add("weibo", "https://weibo.com/2/detail/5367355355694734", "Colorkey珂拉琪唇釉口红腮红促销 2件24.3元赠眼线液笔", "beauty_skincare", "zh", "彩妆 口红 新品 上市 品牌 微博 detail", WB)
add("weibo", "https://weibo.com/2/detail/5356026885050384", "分享#爸爸当家#相关育儿感悟：宝宝是爸妈的治愈存在", "parenting", "zh", "育儿 宝宝 家长 微博 detail", WBM)
add("weibo", "https://weibo.com/2/detail/5360038119997618", "爸爸分享带娃经历：以为享福却累趴 改口愿洗碗感慨不易", "parenting", "zh", "亲子 带娃 育儿 感悟 微博 detail 父母", WB)
q = "年轻人 存钱 理财 观点 微博 detail"
add("weibo", "https://weibo.com/2/detail/5356767946736245", "当代年轻人这么会省钱 是不是攒了不少钱", "finance_news", "zh", q, WB)
add("weibo", "https://weibo.com/ttarticle/p/show?id=2309405187215650586882", "打工人存款50万，什么水平？", "finance_news", "zh", q, WB)
q = "短剧 热播 微博 detail"
add("weibo", "https://weibo.com/2/detail/5367298832728279", "湖里企业出品短剧《鹭岛1981》上线红果抖音热播", "entertainment_drama", "zh", q, WBM)
add("weibo", "https://weibo.com/2/detail/comos:nivawnc8079295", "短剧越骂越火、前途一片争议，流量逻辑如何改写内容行业？一文看懂", "entertainment_drama", "zh", q, WBM)

# ---------------- toutiao ----------------
TO = ["toutiao.com"]
q = "家常菜 做法 美食 头条 文章"
add("toutiao", "https://www.toutiao.com/article/7602485839775466011/", "每天晚餐不重样，5道家常菜做法全解，简单好吃又下饭 - 今日头条", "food_recipes", "zh", q, TO)
add("toutiao", "https://www.toutiao.com/article/7685509596508619303/", "一学就会！10款零失败家常美食，新手也能轻松拿捏 - 今日头条", "food_recipes", "zh", q, TO)
add("toutiao", "https://www.toutiao.com/article/7506909507570729472/", "一篇看懂北京旅行攻略！避坑+干货合集，建议收藏！ - 今日头条", "travel", "zh", "旅游攻略 自由行 景点 今日头条", TO)
add("toutiao", "https://m.toutiao.com/video/7442608827129728282/?app=news_article_dazi&timestamp=1740408807&share_token=9dc07ba1-c9c2-479b-a227-7e79a0027bd6", "浪漫山海: 下一站 广东珠海 珠海真是一座还会再来的城市！#珠海旅游攻略 #风景都在路上 #日月贝 #珠海情侣路风景 #珠海网红景点推荐", "travel", "zh", "m.toutiao.com article 旅游", ["m.toutiao.com"])
q = "手机 AI 大模型 科技 评测 今日头条"
add("toutiao", "https://www.toutiao.com/article/7619256903205470758/", "手机 AI 智能体实测对比！华为小米 OPPO 哪家强？2026 换机必看 - 今日头条", "consumer_tech_ai", "zh", q, TO)
add("toutiao", "https://www.toutiao.com/article/7665960382510432787/", "国行 iPhone、iPad 终于补上 AI 短板！接入阿里千问大模型 - 今日头条", "consumer_tech_ai", "zh", q, TO)
q = "护肤 保湿 精华 美妆 今日头条 文章"
add("toutiao", "https://www.toutiao.com/article/7644834770014224934/", "2026修护精华红榜｜理肤泉超级B5精华实测好评+无踩雷清单全汇总 - 今日头条", "beauty_skincare", "zh", q, TO)
add("toutiao", "https://www.toutiao.com/article/7550949413067113001/", "试了10种去黑头方法，最后发现最简单的才最有用 - 今日头条", "beauty_skincare", "zh", q, TO)
q = "育儿 孩子 家庭教育 父母 今日头条 文章"
add("toutiao", "https://www.toutiao.com/article/7499396654219575808/", "培养好孩子，不是父母严格管教，而是学会“放风筝” - 今日头条", "parenting", "zh", q, TO)
add("toutiao", "https://www.toutiao.com/article/7649187009730249250/", "考前紧绷、考后焦虑是常态！考试结束，家长别再催学，陪孩子放松 - 今日头条", "parenting", "zh", q, TO)
q = "新能源汽车 测评"
add("toutiao", "https://www.toutiao.com/article/7451423034100941327/", "50+热门新能源车冬季实测续航揭秘 小米SU7/理想L6/豹8表现如何？ - 今日头条", "cars_evs", "zh", q, TO)
add("toutiao", "https://www.toutiao.com/article/7650082060765315636/", "10万级新能源SUV推荐：2026年深度对比 - 今日头条", "cars_evs", "zh", q, TO)
q = "理财 存款 利率 下调 普通人 怎么办 今日头条"
add("toutiao", "https://www.toutiao.com/article/7705027403114005046/", "存款利率拐点来了？不少储户已经开始行动 - 今日头条", "finance_news", "zh", q, TO)
add("toutiao", "https://www.toutiao.com/w/1866851830309895/", "棋间运筹帷幄：6月最新！10万存3年，这样存比普通定期多赚1140元 2026年6月全国统一存款利率落地后，90%的人还在按老办法存钱：直接存3年普通定期，利息全听银行说。 其实换个存法，同样10万本金、同样存3年、同样保本保息，利息能多赚1140元，相当于白捡一个月伙食费。今天把计算和实操写得明明白白，照着做就行。 先算一笔明明白白的账 我整理了2026年6月央行、财政部公开的保本类产品...", "finance_news", "zh", q, TO)
q = "短剧 爆款 热播 剧集 综艺 今日头条"
add("toutiao", "https://www.toutiao.com/article/7649290995011469864/", "2026最新短剧推荐：10部爆款刷屏，部部上头不踩雷 - 今日头条", "entertainment_drama", "zh", q, TO)
add("toutiao", "https://www.toutiao.com/article/7528352641009713673/", "红果观看破10亿的短剧，都是谁在做？ - 今日头条", "entertainment_drama", "zh", q, TO)

# ---------------- wechat (mp.weixin.qq.com) ----------------
WX = ["mp.weixin.qq.com"]
q = "家常菜 做法 食谱 公众号 文章"
add("wechat", "https://mp.weixin.qq.com/s?__biz=MzI5MjA1MTMxNA%3D%3D&chksm=f7cdeacec0ba63d890f98834df535b583ea3b501883bdc04f8214a0b5eece452dd1f0d472aff&idx=1&mid=2654866131&scene=21&sn=6d85c7daa3b63fdf0f55e5019d36ccd3", "清醇排骨冬瓜汤，大厨教你去油腻", "food_recipes", "zh", q, WX)
add("wechat", "https://mp.weixin.qq.com/s?__biz=MzIyODE2MjQ3MQ%3D%3D&amp=&amp=&amp=&amp=&amp=&chksm=f04a59afc73dd0b9d88a6930d313dd378dfbb107e6124b92c5509a3b32a565c95f193cf36964&idx=1&mid=2649282407&scene=21&sn=f9014469e8fc8a9e0d0611b43a655d66", "【空气炸锅】懒人版地三鲜", "food_recipes", "zh", q, WX)
q = "旅行 攻略 目的地 推荐 公众号"
add("wechat", "https://mp.weixin.qq.com/s?__biz=MzA3MzE0NTUxNQ%3D%3D&chksm=84faba1db38d330b3f0479a455dc48da9de0c3c28fa455514d307115e77c8a9131f2c1bbf98f&idx=1&mid=2652739545&scene=21&sn=efaeb5b685f523ebf31081db3294de57", "平遥古城住宿攻略-平遥古城九家特色民宿酒店推荐！", "travel", "zh", q, WX)
add("wechat", "https://mp.weixin.qq.com/s?__biz=MzIwNjE3ODMzMQ%3D%3D&amp=&amp=&amp=&amp=&idx=1&mid=2652535479&scene=21&sn=2b11d807c71fa1697d2c9d6b13febb04", "南极攻略｜与我一起到世界尽头", "travel", "zh", q, WX)
q = "AI 大模型 发布 科技 公众号 文章 2026"
add("wechat", "https://mp.weixin.qq.com/s/OQ1HxnAuWkzgI4H_7CykSw", "开源AI 2026", "consumer_tech_ai", "zh", q, WX)
add("wechat", "https://mp.weixin.qq.com/s/w9Jyt5e3BR-lvaIMEjTAew", "2025年年度中文大模型基准测评报告发布！", "consumer_tech_ai", "zh", q, WX)
q = "护肤 成分 精华 防晒 公众号 文章"
add("wechat", "https://mp.weixin.qq.com/s/BWNHuEH9bhHPoZ5qmU0M6w", "【朝医科普】全民科学防晒·护肤第一道防线", "beauty_skincare", "zh", q, WX)
add("wechat", "https://mp.weixin.qq.com/s?__biz=MzkzMjM3OTQ1NA%3D%3D&chksm=c3fc1d9595ed81ae43ba0bc8be37a5aa0b551271b721c8f277a9fd9ea461372f93ad832db79b&idx=4&mid=2247505643&sn=e2814c7941da4eb74cfe616129365722", "【谈药话妆】以酶代酸，背后的真相是什么？", "beauty_skincare", "zh", q, WX)
q = "育儿 经验"
add("wechat", "https://mp.weixin.qq.com/s?__biz=MzIxODk3NDA5OQ%3D%3D&amp=&amp=&amp=&amp=&amp=&chksm=97ee0a2ca099833a2c1be7b25fbf8d44ba4961c96516c8aa6c40c379bed3dbacf8796dd6de48&idx=1&mid=2247686352&scene=21&sn=d993bd4a7514c8ea323a21dc918e5d24", "这些年我攒下的那些带孩子妙招小锦囊，失传就太可惜了", "parenting", "zh", q, WX)
add("wechat", "https://mp.weixin.qq.com/s?__biz=MzUxMzM0MDkyNg%3D%3D&amp=&amp=&amp=&amp=&amp=&chksm=f957e152ce2068442de945a41a5b9346cbc41437e4ed9bdbe55295706789d1ac6488ee651232&idx=1&mid=2247484867&scene=21&sn=1cf4980c02540dbff172ed1f3f60d752", "Potty Train 如厕训练经验分享，让宝宝顺利戒掉尿片", "parenting", "zh", q, WX)
q = "新能源汽车 销量 车型 评测 公众号 文章 2026"
add("wechat", "https://mp.weixin.qq.com/s/-XVHkbYihe_gPo0G71aW-A", "2026年上半年汽车市场分析报告", "cars_evs", "zh", q, WX)
add("wechat", "https://mp.weixin.qq.com/s?__biz=MzI2NTI3NDk2Mg%3D%3D&amp=&amp=&amp=&amp=&amp=&chksm=ea93c5d0dde44cc6deda1ebd44ebad7fe018a22e81da33f40e09df25d39f9e25ac4b97f46148&idx=1&mid=2247669485&scene=21&sn=606527f729d45dd2f8e5c817a786054a", "混动油耗不输日系，国产品牌站起来了，不支持这批新车说不过去！", "cars_evs", "zh", q, WX)
q = "个人理财 存钱 基金 经济 解读 公众号 文章"
add("wechat", "https://mp.weixin.qq.com/s/2ExlaH_bKoajnH92PXcLyQ", "这下降下降再下降的存款利率啊，还能回去吗？", "finance_news", "zh", q, WX)
add("wechat", "https://mp.weixin.qq.com/s?__biz=MzAxMjEyOTYwMQ%3D%3D&chksm=824e9ed97b57edc9111a7a51526b468ec0ed9bb2ff6a5a08492e7bf8b3b12a536b4affba6a53&idx=1&mid=2649994423&sn=9967e62ea9f045a973258d4c604bc07a", "银行长期存款到期？这几只替代基金或可一战！", "finance_news", "zh", q, WX)
q = "短剧 行业 爆款 影视 综艺 公众号 文章 2026"
add("wechat", "https://mp.weixin.qq.com/s/-kwq52cM5gZBSI4He82peA", "短剧难逃「内热外冷」", "entertainment_drama", "zh", q, WX)
add("wechat", "https://mp.weixin.qq.com/s/_bRqGZ7yYBBTaSgXWdOg_w", "无脑、投流、暴利：短剧一天赚1亿，剧组挤爆横店", "entertainment_drama", "zh", q, WX)


# ---------------- post_id derivation (from the URL only) ----------------
def post_id(platform, url):
    u = urlparse(url)
    path = u.path
    qs = parse_qs(u.query, keep_blank_values=True)
    m = None
    if platform == "douyin":
        m = re.search(r"/(?:share/)?video/(\d+)", path)
    elif platform == "xiaohongshu":
        m = re.search(r"/(?:explore|discovery/item)/([0-9a-f]{24})", path)
    elif platform == "bilibili":
        m = re.search(r"/video/(BV[0-9A-Za-z]{10}|av\d+)", path)
    elif platform == "youtube":
        if "v" in qs:
            return qs["v"][0]
        m = re.search(r"/(?:shorts|embed)/([\w-]{11})", path)
    elif platform == "tiktok":
        m = re.search(r"/video/(\d+)", path)
    elif platform == "x":
        m = re.search(r"/status/(\d+)", path)
    elif platform == "weibo":
        m = re.search(r"/2/detail/((?:comos:)?[0-9a-z]+)$", path)
        if not m and "id" in qs:
            return qs["id"][0]
        if not m:
            m = re.search(r"/geoshow/id/(\d+)", path)
    elif platform == "toutiao":
        m = re.search(r"/(?:article|w|video)/(\d+)/?", path)
    elif platform == "wechat":
        m = re.search(r"^/s/([\w-]{22})$", path)
        if not m and "__biz" in qs:
            return "__biz=%s&mid=%s&idx=%s" % (unquote(qs["__biz"][0]), qs["mid"][0], qs["idx"][0])
    return m.group(1) if m else None

DOMAINS = {"food_recipes", "travel", "consumer_tech_ai", "beauty_skincare", "parenting",
           "cars_evs", "finance_news", "entertainment_drama"}
LANGS = {"zh", "en", "ja"}
HOSTS = {"douyin": ("douyin.com",), "xiaohongshu": ("xiaohongshu.com", "rednote.com"),
         "bilibili": ("bilibili.com",), "youtube": ("youtube.com",), "tiktok": ("tiktok.com",),
         "x": ("x.com", "twitter.com"), "weibo": ("weibo.com", "weibo.cn"),
         "toutiao": ("toutiao.com",), "wechat": ("mp.weixin.qq.com",)}

seen, rows, errs = set(), [], []
for r in R:
    host = urlparse(r["url"]).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in HOSTS[r["platform"]]):
        errs.append("host mismatch %s" % r["url"])
    if r["domain"] not in DOMAINS: errs.append("bad domain %s" % r["url"])
    if r["language"] not in LANGS: errs.append("bad lang %s" % r["url"])
    if r["url"] in seen: errs.append("dup url %s" % r["url"])
    seen.add(r["url"])
    pid = post_id(r["platform"], r["url"])
    if pid is None: errs.append("no post_id %s" % r["url"])
    rows.append({
        "platform": r["platform"],
        "url": r["url"],
        "post_id": pid,
        "title_or_snippet": r["title"],
        "domain": r["domain"],
        "language": r["language"],
        "query": r["query"],
        "query_allowed_domains": r["allowed"],
        "found_at": FOUND_AT,
        "engine": ENGINE,
    })

# duplicate post ids within a platform would mean the same post twice under different URL forms
pids = collections.Counter((x["platform"], x["post_id"]) for x in rows)
for k, v in pids.items():
    if v > 1: errs.append("dup post_id %s" % (k,))

if errs:
    print("\n".join(errs)); sys.exit(1)

with open(OUT, "w", encoding="utf-8") as f:
    for x in rows:
        f.write(json.dumps(x, ensure_ascii=False) + "\n")

by_p = collections.Counter(x["platform"] for x in rows)
by_d = collections.Counter(x["domain"] for x in rows)
by_l = collections.Counter(x["language"] for x in rows)
grid = collections.defaultdict(collections.Counter)
for x in rows: grid[x["platform"]][x["domain"]] += 1
print("total", len(rows))
print("platform", dict(by_p))
print("domain", dict(by_d))
print("language", dict(by_l))
order = ["food_recipes", "travel", "consumer_tech_ai", "beauty_skincare", "parenting", "cars_evs", "finance_news", "entertainment_drama"]
print("grid platform x domain:", " | ".join(order))
for p in ["douyin", "xiaohongshu", "bilibili", "youtube", "tiktok", "weibo", "toutiao", "x", "wechat"]:
    print(p, [grid[p][d] for d in order])
lang_grid = collections.defaultdict(collections.Counter)
for x in rows: lang_grid[x["platform"]][x["language"]] += 1
print("lang by platform", {p: dict(c) for p, c in lang_grid.items()})
