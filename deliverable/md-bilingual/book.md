# How Large Language Models Work · 中英双语版

> 由 book-agent 翻译管线生成

## 目录

- [第 1 章 · 宏观图景：什么是大语言模型？](#ch1)
- [第 2 章 · 分词器：大语言模型如何看待世界](#ch2)
- [第 3 章 · Transformer 架构：输入如何转换为输出](#ch3)
- [第 4 章 · LLM 如何学习](#ch4)
- [第 5 章 · 如何约束 LLM 的行为](#ch5)
- [第 6 章 · 超越自然语言处理](#ch6)
- [第 7 章 · LLM 的误解、局限与新兴能力](#ch7)
- [第 8 章 · 用大语言模型设计解决方案](#ch8)
- [第 9 章 · 构建与使用 LLM 的伦理](#ch9)

<a id="ch1"></a>

## 第 1 章: 宏观图景：什么是大语言模型？

宏观视角：什么是LLM？

### 本章涵盖

- Transformer 与大型语言模型

- LLM 的通俗工作原理

- 人类与机器表征语言的不同方式

- ChatGPT 等工具表现优异的原因

- 理解使用LLM的局限与顾虑

<details>
<summary>英文原文</summary>

- Transformers and large language models are
- How LLMs work in plain language
- How humans and machines represent languages differently
- Why tools like ChatGPT perform so well
- Understanding the limitations and concerns of using LLMs

</details>

如今，机器学习（ML）、深度学习（DL）和人工智能（AI）等术语的热度达到了历史新高。这些术语最初在公众视野中广泛曝光，很大程度上源于OpenAI公司开发的生成式AI产品ChatGPT。现在，我们每天都能在新闻中看到各种生成式AI产品，如Google的Gemini、Microsoft的Copilot、Meta的Llama、Anthropic的Claude，以及DeepSeek等新秀。仿佛一夜之间，计算机在对话、学习和执行复杂任务方面的能力实现了巨大飞跃。新的生成式AI公司不断涌现，现有企业也公开向该领域投入数十亿美元。该领域的技术正以令人疯狂的速度演进。

<details>
<summary>英文原文</summary>

The hype around terms such as machine learning (ML), deep learning (DL), and artificial intelligence (AI) has reached record levels. Much of the initial public exposure to these terms was driven by a product called ChatGPT, a form of generative AI built by a company called OpenAI. We now see generative AI offerings such as Gemini from Google, Copilot from Microsoft, Llama from Meta, Claude from Anthropic, and newcomers like DeepSeek in the daily news. Seemingly overnight, the ability of computers to talk, learn, and perform complex tasks has taken a dramatic leap forward. New generative AI companies are forming, and existing firms are publicly investing billions of dollars in the field. The technology in this space is evolving at a maddening pace.

</details>

本书旨在通过揭开ChatGPT及相关技术背后的奥秘，帮助您理解这个新世界。我们将介绍理解其内部运作所需的知识，以及组件（数据和算法）如何层层叠加，构建出我们所使用的工具。我们还将讨论各种场景：有些场景中，这项技术可以成为更广泛系统的基石；而在另一些场景中，基于大型语言模型（LLMs）的系统可能并非最佳选择。

读完本书后，您将理解ChatGPT这类生成式AI的真正本质、它能做什么和不能做什么，以及更重要的是，其局限性背后的“缘由”。掌握这些知识后，无论您是作为用户、软件开发者，还是作为决定是否以及如何将这项技术融入产品或运营的企业决策者，都能更有效地运用这一系列技术。这一基础也将为您深入研究该领域提供跳板，让您能够理解深度的研究和其他著作。

<details>
<summary>英文原文</summary>

This book aims to help you make sense of this new world by dispelling the mystery behind what makes ChatGPT and related technologies work. We will cover the knowledge necessary to understand their inner workings and how the components (data and algorithms) stack together to create the tools we use. We’ll also discuss various cases where this technology can form the cornerstone of a broader system and others where systems based on large language models (LLMs) may be a poor choice.

After reading this book, you’ll understand what generative AI like ChatGPT really is, what it can and can’t do, and, importantly, the “why” behind its limitations. With this knowledge, you’ll be a more effective consumer of this family of technology, whether as a user, a software developer, or a business decision maker in organizations deciding whether and, if so, how to incorporate it into your products or operations. This foundation will also serve as a launchpad for deeper study into the field by providing knowledge that will allow you to understand in-depth research and other works.

</details>

### 1.1 生成式AI的上下文

首先，我们需要更具体地明确，当我们谈论LLM、GPT以及依赖它们各种工具时，究竟在讨论什么。ChatGPT中的GPT代表生成式预训练Transformer。这些词汇中的每一个在ChatGPT的语境中都有特定含义。我们将在后续章节详细讨论“预训练”和“Transformer”的含义，但本章先从“生成式”在这一语境中的意义讲起。像ChatGPT这类AI聊天机器人是生成式AI的一种形式。广义上，生成式AI是一种能够基于过去观察的数据，并受人们认为满意和准确输出的影响，创造或生成各种媒体（如文本、图像、音频和视频）的软件。例如，如果向ChatGPT输入提示“Write a haiku about snow falling on pines”，它就会利用训练数据中所有关于俳句、雪、松树以及其他诗歌形式的知识，生成一首新颖的俳句，如图1.1所示。

<details>
<summary>英文原文</summary>

First, we need to get more specific about what we are discussing when we talk about LLMs, GPTs, and the various tools that rely on them. The GPT in ChatGPT stands for Generative Pretrained Transformer. Each of these words bears a particular meaning in the context of ChatGPT. We’ll dedicate future chapters to discussing what pretrained and transformer mean, but we start here by discussing what generative means in this context.

AI chatbots like ChatGPT are a form of generative AI. Broadly, generative AI is software capable of creating, or generating, various media (e.g., text, images, audio, and video) based on data it has observed in the past and influenced by what people consider to be pleasing and accurate output. For example, if ChatGPT is prompted with “Write a haiku about snow falling on pines,” it will use all of the data it was trained with about haikus, snow, pines, and other forms of poetry to generate a novel haiku as shown in figure 1.1

</details>

![图 1.1 一个由 ChatGPT 生成的简单俳句](assets/fig-1-1-ecb14124ce.png)

*图1.1 一个由 ChatGPT 生成的简单俳句*

从根本上说，这些系统是生成新输出的机器学习模型，因此生成式AI是一个恰当的描述。图1.2展示了一些可能的输入和输出。虽然ChatGPT主要处理文本输入和输出，但它对音频和图像等不同数据类型也有实验性支持。然而，根据我们的定义，你可以想象很多不同种类的算法和任务都属于生成式AI的范畴。

<details>
<summary>英文原文</summary>

Fundamentally, these systems are machine learning models that generate new output, so generative AI is an appropriate description. Some possible inputs and outputs are demonstrated in figure 1.2. While ChatGPT deals primarily with text as input and output, it also has more experimental support for different data types, such as audio and images. However, from our definition, you can imagine that many different kinds of algorithms and tasks fall into the description of generative AI.

</details>

![图 1.2 生成式AI接收输入（数字、文本、图像），生成新的输出（通常为文本或图像）。任何输入与输出的组合都是可能的，输出的性质取决于算法训练的目标。它可以是添加细节、改写得更简短、补全缺失部分等。](assets/fig-1-2-796f14358b.jpg)

*图1.2 生成式AI接收输入（数字、文本、图像），生成新的输出（通常为文本或图像）。任何输入与输出的组合都是可能的，输出的性质取决于算法训练的目标。它可以是添加细节、改写得更简短、补全缺失部分等。*

![图 1.3 一张概览图，展示了你将熟悉的各个术语及其相互关系。生成式AI描述的是功能性：即生成内容的功能，并利用AI技术来实现这一目标。](assets/fig-1-3-a353ac77c0.png)

*图1.3 一张概览图，展示了你将熟悉的各个术语及其相互关系。生成式AI描述的是功能性：即生成内容的功能，并利用AI技术来实现这一目标。*

注意：视觉和语言并非生成式AI的唯一选择。

音频生成（比如文本转语音，例如GPS报出路名）、棋类游戏（如下棋）甚至蛋白质折叠都曾应用生成式AI。本书将主要聚焦于文本和语言，因为它们是GPT和LLM处理的主要数据类型。

<details>
<summary>英文原文</summary>

NOTE Vision and language are not the only options for generative AI. Audio generation (think text-to-speech, such as when your GPS speaks out the street names), playing board games like chess, and even protein folding have used generative AI. This book will stick mostly to text and language since those are the primary data types employed by GPTs and LLMs.

</details>

顾名思义，“大”语言模型就说明其规模不小。据传ChatGPT包含1.76万亿个参数[1]，这些参数决定了它的行为方式。每个参数通常以浮点数（带小数点的数）存储，占用4字节。这意味着模型本身需要7TB的内存来承载。这个尺寸超出了大多数个人计算机的RAM容量，更不用说最强的、显存仅80GB的图形处理器（GPU）了。GPU是专用硬件组件，擅长执行使大语言模型成为可能的数学运算。目前，构建大语言模型需要大量GPU，因此我们已经在讨论跨多台计算机的大量计算基础设施和复杂性。相比之下，更普通的语言模型大多数情况下只有2GB或更小——小5000多倍，在更标准的硬件上构建和使用这种模型时，这个尺寸要合理得多。

<details>
<summary>英文原文</summary>

As the name large implies, these models are not small. ChatGPT specifically is rumored [1] to contain 1.76 trillion parameters that are used to dictate the way it behaves. Each parameter is typically stored as a floating point number (a number with a decimal point) that uses 4 bytes for storage. That means the model itself takes 7 terabytes to hold in memory. This size is larger than most people’s computers could fit in RAM, let alone inside the most powerful graphics processing units (GPUs) with 80 gigabytes of memory. GPUs are special-purpose hardware components that excel in performing the mathematical operations that make LLMs possible. Currently, many GPUs are required when making LLMs, so we are already discussing a lot of computational infrastructure and complexity over multiple machines to build an LLM. In contrast, more run-of-the-mill language models would be 2 GB or less in most cases—over 5,000× smaller, a much more reasonable size when considering building and using such a model on more standard hardware.

</details>

许多研究人员正在探索如何让LLM占用更少的内存。有时，这包括利用称为“混合精度”[2]的方法，以少于4字节存储参数的技术。这种方法用2字节或更少存储部分LLM参数，在精度和内存效率之间进行权衡。最终，对精度的影响通常可以忽略不计。这种优化是研究人员为使LLM更高效利用资源而采取的众多措施之一。

<details>
<summary>英文原文</summary>

Many researchers are investigating ways to make LLMs consume less memory. Sometimes, this includes techniques that require less than 4 bytes to store a para-meter utilizing a method called “mixed-precision” [2]. This approach stores some LLM parameters using 2 bytes or fewer and presents a tradeoff between accuracy and memory efficiency. In the end, the effect on accuracy is often negligible. This optimization is one of many that researchers make to make LLMs more resource efficient.

</details>

GPU 的替代方案 虽然 GPU 目前是训练大语言模型最常用的硬件，但它们并非唯一选择。越来越多的公司正在开发专用硬件，为训练机器学习模型提供通用优势。例如，谷歌于 2018 年将张量处理单元 (TPU) [3] 作为 Google Cloud Platform (GCP) 的一部分向公众开放。虽然 TPU 的计算能力通常低于 GPU，但其专用架构使其在特定机器学习任务上表现优于 GPU。

<details>
<summary>英文原文</summary>

GPU alternatives While GPUs are currently the most frequently used hardware to train LLMs, they aren’t the only option available. Increasingly, companies are developing special-purpose hardware that offers general advantages for training machine learning models. For example, in 2018, Google made its Tensor Processing Unit (TPU) [3] available for public use as a part of the Google Cloud Platform (GCP). While TPUs generally have less computing capacity than GPUs, their specialized architecture allows them to perform better than GPUs for specific machine learning tasks.

</details>

### 1.2 你将学到什么

在本书中，我们将解释大语言模型（LLM）的工作原理，并为您提供理解它们所需的词汇。读完本书后，您将对大语言模型是什么以及其运行的关键步骤有一个通俗的理解。此外，您还将对大语言模型合理可做的事情有所了解，特别是与部署或使用相关的考量。我们将讨论大语言模型基本局限性的要点，并提供如何规避这些局限性的技巧，或者说明何时应完全避免使用大语言模型乃至更广泛的生成式AI。

请记住，用于构建ChatGPT、Claude或Gemini的Transformer组合细节十分微妙，而本书主要关注所有这些系统的共同点。事实上，我们无法了解这些大语言模型之间的一些实际差异，因为尽管商业大语言模型提供商分享了大量关于其模型的信息，但他们仍未分享某些可能被视为商业机密的信息。

鉴于基于Transformer的大语言模型将对世界产生的影响，我们特意将本书定位为面向广泛读者。未来几年，各种背景的程序员、高管、经理、销售、艺术家、作家、出版商等众多人士都将不得不与LLM互动，或者其工作将受到LLM的影响。因此，我们将假设亲爱的读者您具备最基础的编程背景，但熟悉编程的基本构造：逻辑、函数，甚至可能了解一些数据结构。您也无需是数学家；我们会在必要时展示一些有用的数学知识，但理解LLM工作原理时这些是可选的。

这种方法意味着本书中出现的代码非常少。如果您想直接深入构建和使用LLM，Manning出版社的其他书籍，如Sebastian Raschka的《从零开始构建大语言模型》（2024年）或Edward Raff的《深度学习内幕》（2022年），将补充本书介绍的内容。然而，如果你想了解为什么你正在使用的LLM会产生异常输出、你的团队如何能使用LLM、或者哪些情况应避免使用LLM，又或者你有一位机器学习背景薄弱但需要达到流利对话程度的同事，那么本书正是你和你的同事所需要的。

<details>
<summary>英文原文</summary>

Throughout this book, we will explain how LLMs work and equip you with the vocabulary needed to understand them. Once you’ve finished reading, you will have a conversational understanding of what an LLM is and the critical steps involved in its operation. Additionally, you will have some perspective on what an LLM reasonably can do, especially the considerations related to deploying or using one. We will discuss salient points about the fundamental limitations of LLMs and provide tips on how to design around them or when LLMs and, more broadly, generative AI should be avoided entirely.

Keep in mind that the details of how transformers are combined to build ChatGPT, Claude, or Gemini are nuanced, and this book primarily focuses on what all of these systems have in common. In fact, we can’t know some of the actual differences between these LLMs because although commercial LLM providers have shared a great deal of information about their models, they have not shared some pieces of information, likely considered trade secrets.

Due to the effect that transformer-based LLMs will have on the world, we’re purposely focusing on a wide audience for this book. Programmers of all backgrounds, executives, managers, sales staff, artists, writers, publishers, and many more will have to interact with or have their jobs affected by LLMs over the coming years. So we are going to assume you, dear reader, have a minimal coding background but are familiar with the basic constructs of coding: logic, functions, and maybe even some data structures. You also do not need to be a mathematician; we will show you a bit of math where it is helpful, but it will be optional in building an understanding of how LLMs work.

This approach means that very little code will be presented in this book. If you want to dive directly into building and using an LLM, other books in the Manning catalog, such as Sebastian Raschka’s Build a Large Language Model from Scratch (2024) or Edward Raff’s Inside Deep Learning (2022), will complement the material presented here. However, if you want to understand why the LLM you are using has unusual outputs, how your team might be able to use an LLM, or where to avoid using an LLM, or if you have a colleague with little machine learning background who needs to get conversationally competent, this is the book you and your colleague need.

</details>

具体而言，本书第一部分聚焦于LLM的功能：它们的输入和输出，如何将输入转化为输出，以及我们如何约束这些输出的性质。第二部分则聚焦于人的行为：人们如何与技术交互，以及这对使用生成式AI带来的风险。同样，我们还将讨论使用和构建LLM时出现的一些伦理考量。

<details>
<summary>英文原文</summary>

In particular, the first part of this book focuses on what LLMs do: their inputs and outputs, converting inputs to outputs, and how we constrain the nature of those outputs. In the second part, we focus on what humans do: how people interact with technology and what risks this creates for using generative AI. Similarly, we’ll discuss some ethical considerations that arise when using and building LLMs.

</details>

**训练LLM成本高昂**

训练大型语言模型（LLM）对大多数人而言并不现实——这至少需要10万美元的投资，若想与OpenAI竞争，投入甚至高达1亿美元。与此同时，训练LLM所需的资源也在不断演变。因此，我们不会带你了解当前训练LLM的具体流程，而是聚焦于更具长期价值的内容——那些我们认为在未来数年依然有效的实用知识，而非几个月内就可能过时的示例代码。

<details>
<summary>英文原文</summary>

**Training LLMs is expensive**

Training an LLM is not realistically possible for most people; it is a ≥$100, 000 investment at a minimum and would be a $100 million effort to try to compete with OpenAI. At the same time, the resources available for training LLMs are constantly evolving. As a result, instead of walking you through what training an LLM looks like today, we focus on content with a longer shelf life—helpful knowledge that we believe will be valid years from now instead of example code that could be out of date in just a few months.

</details>

### 1.3 介绍LLM的工作原理

生成式人工智能（GAI或称GenAI）即将改变我们生产和交互信息的方式。2022年11月ChatGPT的推出突显了现代AI的能力，并吸引了全球相当一部分人的关注。目前，你可以免费注册网址 https://chat.openai.com/ 进行尝试。

如果你输入提示文本“用两句话总结以下文本”，然后接本章所有引言文本，你将得到类似下面的内容。

“近期人们对人工智能，尤其是OpenAI的ChatGPT这样的大语言模型（LLM）的关注激增，突显了它们在自然语言处理方面的强大能力。本书旨在让读者对话式地了解LLM，包括其操作细节、潜在应用、局限性以及使用中的伦理考量，假定读者仅具备基本的编程概念和极少的数学背景。“这令人印象深刻，对普通观众来说，这种能力似乎从天而降。”当你访问OpenAI的网站并注册ChatGPT时，你可能会注意到类似图1.4所示的选项。

正如GPT-4这个名字所暗示的，截至撰写本文时，OpenAI正在开发其第四代GPT模型。像GPT-4这样的LLM是机器学习研究中一个成熟的领域，旨在创建能够综合信息并对其做出反应、生成看似人类输出的算法。这一能力开启了人机交互的多个领域，这些领域之前只存在于科幻小说中。ChatGPT编码的语言表示能力使其能够进行令人信服的对话、遵循指令、生成摘要、回答问题、创建内容以及更多应用。事实上，这项技术的许多可能应用可能还不存在，因为

<details>
<summary>英文原文</summary>

Generative AI (GAI or GenAI) is poised to change how we produce and interact with information. The introduction of ChatGPT in November 2022 highlighted the capabilities of modern AI and fascinated a significant portion of the world. Currently, you can sign up for free at https://chat.openai.com/ to try it out. If you enter the text prompt “Summarize the following text in two sentences,” followed by all of the introductory text from this chapter, you will get something similar to the following.

“The recent surge in attention towards artificial intelligence, particularly large language models (LLMs) like ChatGPT from OpenAI, has highlighted their vast capabilities in natural language processing. This book aims to provide readers with a conversational understanding of LLMs, their operational intricacies, potential applications, limitations, and the ethical considerations surrounding their use while assuming only a basic familiarity with coding concepts and minimal mathematical background. That’s pretty impressive, and to a casual audience, it may seem like this capability has come out of nowhere.”

When you visit OpenAI’s website and sign up for ChatGPT, you may notice an option similar to that shown in figure 1.4. As the name GPT-4 implies, Open AI is, as of this writing, working on its fourth generation of GPT models. LLMs like GPT-4 are a well-established area of ML research in creating algorithms that can synthesize and react to information and produce outputs that appear human generated. This ability unlocks several areas of interaction between people and machines that previously existed only in science fiction. The strength of the language representation encoded into ChatGPT enables convincing dialog, instruction following, summary generation, question answering, content creation, and many more applications. Indeed, it is likely that many possible applications of this technology do not yet exist because

</details>

![图 1.4 当你注册OpenAI的ChatGPT时，你有两个选择：免费的GPT-3.5模型，或者付费的GPT-4模型。](assets/fig-1-4-dfd099cc8e.png)

*图1.4 当你注册OpenAI的ChatGPT时，你有两个选择：免费的GPT-3.5模型，或者付费的GPT-4模型。*

关键的一点是，这项技术并非凭空出现，而是过去十年机器学习逐年显著进步的成果。因此，我们已经对LLM的工作原理及其可能的失败方式有了相当多的了解。我们假设读者只需具备最低限度的背景知识，这样你就可以把这本书送给朋友和家人。（其中一位作者希望这本书也能送给他的母亲——她为自己感到非常骄傲，尽管并不确切知道儿子的工作是什么。）因此，在深入探讨之前，我们需要填补背景知识上可能存在的巨大空白。第一章旨在提供这些背景，以便下一章可以开始回答这个问题：计算机究竟是如何为本书引言生成摘要的？

<details>
<summary>英文原文</summary>

The critical factor for you, the reader, is that this technology did not come out of nowhere but is the result of steady progress over the past decade of dramatic year-over-year improvements in machine learning. Consequently, we already know quite a lot about how LLMs work and the ways that they can fail. We are assuming a minimal background so that you can give this book to your friends and family. (One of the authors is hopeful that they can give this book to their mother, who is very proud of them even if she does not know precisely what their job is.) As a result, we need to cover a potentially large gap in the background before we dive in. This first chapter aims to give you that background so the next chapter can begin the process of answering this question: How on earth did a computer summarize the introduction of this book?

</details>

### 1.4 究竟什么是智能？

从营销角度来看，人工智能是一个绝妙的名称，尽管它最初被用作整个学术研究领域的名称。这种做法导致了一个微妙的问题，使人们对AI的工作方式产生了错误的心理模型。我们将尽量避免强化这种模型。为了解释原因，我们将讨论为什么人工智能并非一个那么好的名称。我们可以通过考虑一个简单的问题来轻松证明这一点：什么是智能？

<details>
<summary>英文原文</summary>

Artificial intelligence is an excellent name from a marketing perspective, although it was originally used as the name for an entire field of academic research. This practice has led to a subtle problem that gives people a false mental model of how AI works. We are going to try to avoid reinforcing this model. To explain why, we will discuss why artificial intelligence is not such a great name. We can demonstrate this easily by considering a simple question: What is intelligence?

</details>

你可能会认为像智商（IQ）测试这样的东西能帮助我们回答这个问题。智商测试与学业成绩等多种结果有很强的相关性，但并未给出智能的客观定义。研究表明，先天（遗传）和后天（环境）因素在一定程度上影响一个人的智商。同样可疑的是，我们竟然能将智能简化为一个简单的数字——毕竟，我们经常批评某人只有“书本知识”而没有“街头智慧”。即便我们知道什么是智能，是什么让它成为“人工”的呢？智能难道还有人工调味料和食用色素吗？

归根结底，智商测试衡量的是你在有限能力范围内的表现，主要是在时间限制下解决特定逻辑题的能力，但这并不能帮助我们理解智能的本质。事实是，我们对智能是什么并没有完美的理解。

人工智能领域长期以来一直试图让计算机——这些刻板、确定、遵循规则的机器——执行人类能做到但无法给出精确定义或指示的特定任务。例如，如果我们想让计算机数到1000并打印出所有能被5整除的数，我们可以编写详细的指令，几乎任何程序员都能将其转化为代码。但如果我请你编写一个程序，试图检测任意一张图片中是否有猫，那就是一个完全不同的挑战了。你需要以某种方式精确定义什么是猫，以及检测猫的所有细节。我们究竟如何编写代码来识别并区分猫的胡须和狗的胡须？当猫没有胡须时，我们又该如何成功识别它？说到底，这并不容易做到。

然而，由于 AI 和 ML 一直专注于这些人类能做但难以明确描述的任务，用类比来描述 AI 和 ML 算法变得尤为常见。为了让计算机检测猫，我们提供了成千上万张猫的图像和不是猫的图像作为示例。然后我们运行多种算法中的一种，通过特定、详细的数学过程来区分猫和其他事物。但在技术术语中，我们称这个过程为学习。当模型在新的图像中未能检测到猫，因为那是一只狮子，而狮子不在最初的猫列表中时，我们常说模型“不理解”狮子。

的确，每当我们试图向朋友解释某事时，我们经常使用双方都熟悉的共同概念作为类比。因为 AI 和 ML 广泛关注于复制人类执行任务的能力，类比往往使用暗示人类实际认知功能的语言。随着 LLM 展现出接近人类水平的能力，这些类比与其说有帮助，不如说更麻烦，因为人们过度解读它们，开始相信它们比实际含义更多。

因此，我们将谨慎使用类比，并提醒读者不要过度解读任何类比。像“学习”这样的术语是值得理解的技术行话，但我们希望你能警惕它们可能暗示的含义。

<details>
<summary>英文原文</summary>

You might think that something like an intelligence quotient (IQ) test would help us answer that question. IQ tests have a strong correlation with numerous outcomes like school performance, but they do not give us an objective definition of intelligence. Studies show that some amount of nature (hereditary) and nurture (environment) affect a person’s IQ. It should also seem suspicious that we can boil down intelligence into something as simple as one number—after all, we often scold people for being only “book smart” but not “street smart.” Even if we knew what intelligence was, what would make it artificial? Does intelligence have manufactured flavorings and food colorings?

The bottom line is that IQ tests measure your ability to perform a finite set of capabilities, mostly some specific types of logic puzzles under time constraints, but they don’t help us understand the fundamental nature of intelligence. The truth is that there is no perfect understanding of what intelligence is. The field of AI has long been trying to get computers, which are rigid, deterministic, rule-following machines, to perform specific tasks that humans can do but can’t give precise definitions or instructions to do. For example, if we want a computer to count to 1,000 and print out every number divisible by 5, we can write detailed instructions that almost any programmer can convert to code. But if I ask you to write a program that attempts to detect if an arbitrary picture has a cat in it, that’s quite a different challenge. You need to somehow precisely define what a cat is and then all the minutia of how to detect one. How exactly do we write code to find and differentiate between cat whiskers and dog whiskers? How do we successfully recognize a cat when it does not have whiskers? When it comes down to it, it isn’t easy to do. However, because AI and ML have focused on these hard-to-specify tasks that humans can perform, describing AI and ML algorithms using analogies has become especially common. To get a computer to detect cats, we provide thousands upon thousands of examples of images that are cats and images that are not cats. We then run one of many various algorithms with a specific, detailed, mathematical process for differentiating cats from the rest of the world. But in the technical vocabulary, we call this process learning. When the model fails to detect a cat in a new image because it is a lion and lions were not in the original list of cats, we often say that the model didn’t understand lions.

Indeed, whenever we try to explain something to friends, we often use analogies to shared concepts that we are both familiar with. Because AI and ML are broadly focused on replicating human abilities to perform tasks, the analogies often use language that implies the literal cognitive functions of a human. As LLMs demonstrate capabilities at a level close to what humans can do, these analogies become more troublesome than helpful because people read too deeply into them and begin to believe that they mean more than they do.

For this reason, we will be careful with our analogies and caution the reader about following any analogies too far. Some terms, like learning, are technical jargon worth understanding, but we want you to be on your guard about what they might imply.

</details>

在某些情况下，本书中仍然会使用类比，但我们会尽量明确解释这类类比的边界。

<details>
<summary>英文原文</summary>

In some cases, analogies are still helpful in this book, but we will try to be explicit about the boundaries of how to interpret such analogies.

</details>

### 1.5 人类与机器表征语言的差异

注意：大脑结构的抽象已被证明在多个领域中具有实用价值。

神经网络在语言、视觉、学习和模式识别方面取得了令人难以置信的进展。神经机器学习算法的进步、数字数据的极度激增以及 GPU 等计算机硬件的爆发式增长，这些因素的融合促成了如今 ChatGPT 的实现。

<details>
<summary>英文原文</summary>

NOTE Abstractions of the brain’s structure have proven useful across many domains. Neural networks have demonstrated incredible progress in language, vision, learning, and pattern recognition. The convergence of advancements in neural machine learning algorithms, the extreme proliferation of digital data, and an explosion of computer hardware, such as GPUs, have led to the advancements that make ChatGPT possible today.

</details>

从这个讨论中得出的关键细节是，作为人类，你对语言有一种与生俱来的理解，这种理解是随着时间的推移而习得的。你对语言的学习和使用是交互式的。通过进化，我们似乎都有相对一致的学习和相互交流的方式。要了解更多关于这个概念的信息，可以查阅语言学家诺姆·乔姆斯基提出的普遍语法理论。与人类不同，LLM的语言表征是通过静态过程学习得到的。当你与Claude或ChatGPT对话时，它们机械地参与对话，尽管它们之前从未有过对话经历。

LLM学习到的语言表征质量可能很高，但并非没有错误。这种表征是可操控的，我们可以通过特定方式改变LLM的行为，限制其感知或生成的内容。理解LLM通过从示例中推断出的关系来表征语言，有助于我们保持现实的期望。如果你要使用LLM，它出错时有多危险？你如何利用语言表征来构建产品或避免不良后果？这些就是本书将讨论的一些高层次问题。

<details>
<summary>英文原文</summary>

The critical detail to take from this discussion is that you, as a human, have an innate understanding of language you have learned over time. Your learning and use of language are interactive. Through evolution, we all seem to have relatively consistent ways of learning and communicating with each other. To find out more about this concept, look into the theory of universal grammar introduced by linguist Noam Chomsky. Unlike people, LLMs have a representation of language that is learned via a static process. When you have a conversation with Claude or ChatGPT, it mechanically participates in a dialog with you despite having never been in a conversation before. The representation of language an LLM learns can be high quality, but it is not error-free. It is manipulable in that we can alter the behavior of LLMs in specific ways to limit what they are aware of or what they produce. Understanding that LLMs represent language using relationships inferred from examples helps us maintain realistic expectations. If you are going to use an LLM, how dangerous is it if it is wrong? How can you work with the representation of language to build a product or avoid a bad outcome? These are some of the high-level concerns we will discuss throughout this book.

</details>

### 1.6 生成式预训练变换器与同类模型——“生成式预训练”术语

Transformer 并非由 OpenAI 发明，而是指他们在 2018 年引入的一种新型模型，该模型采用了一种名为 Transformer 的神经网络组件。虽然最初的 GPT 模型（GPT-1）已不再使用，但预训练和 Transformer 的核心思想已成为近期生成式 AI 革命以及 Claude、Gemini、Llama、Copilot 等工具的基石。

同样重要的是要认识到，这些基于 GPT 的 AI 工具只是 LLM 算法研究和应用广阔领域中的一个例子。除了 ChatGPT 的发布，我们还看到了 LLM 的惊人激增。一些 LLM，如 EleutherAI 和 BigScience 研究研讨会发布的那些，是免费向公众开放的，以促进研究和探索应用。如前所述，Meta、微软和谷歌等公司发布了其他具有更严格许可条款的 LLM。任何人都可以用来构建应用程序或系统的公开 LLM，有时被称为基础模型，它们催生了一个由研究人员、爱好者和公司组成的充满活力的社区，探索 LLM 和生成式 AI 带来的应用、局限和机遇。本书教授的概念几乎统一适用于所有 LLM。每个 LLM 都使用与 ChatGPT 类似（若非完全相同）的结构生成输出。

一本书要包含适用于许多模型的通用摘要似乎是不可能的。然而，这是可能的，原因有几个，其中最重要的是我们不会深入到从头编写 LLM 所需的细节程度。自然，ChatGPT 和其他商业 LLM 的某些部分仍然是商业秘密。因此，我们的范围和描述有意概括了当今所有生成式 LLM 的最常见方面。

我们可以给出如此广泛适用的摘要的第二个原因是 LLM 的本质。虽然确实可以对它们的构建和操作进行许多调整，但该领域的研究人员一致发现，最重要的细节如下：

<details>
<summary>英文原文</summary>

Transformer was invented by OpenAI to talk about a new type of model they introduced in 2018 that incorporates a type of neural network component known as a transformer. While the original GPT model (GPT-1) is no longer used, the core underlying ideas of pretraining and transformers have become core pillars of the recent revolution in generative AI and tools like Claude, Gemini, Llama, and Copilot. It is also essential to recognize that these GPT-based AI tools are only one example of an expansive domain of algorithmic research and application of LLMs. Outside of the release of ChatGPT, we have observed an incredible proliferation of LLMs. Some LLMs, like those released by EleutherAI and the BigScience Research Workshop, are freely available to the public to advance research and explore applications. Corpo-rations like Meta, Microsoft, and Google, as we’ve mentioned, have released other LLMs with more restrictive licensing terms. Publicly available LLMs that anyone can use to build an application or system, sometimes called foundation models, have created a vibrant community of researchers, hobbyists, and companies exploring the applications, limitations, and opportunities LLMs and generative AI create. The concepts we teach in this book apply nearly uniformly to all LLMs. Each of these produce output using structures similar, if not identical, to those found in ChatGPT. It may seem impossible for one book to contain a general summary applicable to many models. However, it is possible for a few reasons, one of the most important being that we will not go to the level of depth necessary to code an LLM yourself from scratch. Naturally, there are parts of ChatGPT and other commercial LLMs that remain trade secrets. As a result, our scope and descriptions are intentionally generalized to the most common aspects of all generative LLMs today. The second reason we can give such a broadly applicable summary is the nature of LLMs. While it’s true that many tweaks can be made to how they are built and operate, researchers in the field consistently find that the details that matter the most are the following:

</details>

模型规模有多大？能否进一步扩大？构建模型使用了多少数据？能否获取更多？

<details>
<summary>英文原文</summary>

How large is the model, and can you make it larger? How much data was used to build the model, and can you get more?

</details>

这些观点可能会让那些自认为拥有重要见解或设计、能显著改善这些LLMs工作方式的研究人员感到沮丧，因为在许多情况下，同样的改进只需通过“做大”或用更多数据或参数构建模型就能轻松实现。扩大模型和数据池的规模是围绕使用和构建LLMs的许多伦理问题的关键组成部分，我们将在第9章中讨论。

<details>
<summary>英文原文</summary>

These points can be frustrating for researchers who like to think they have vital insights or designs that meaningfully improve how these LLMs work and operate because, in many cases, the same improvement could be obtained just as easily by “making it bigger” or building a model with more data or more parameters instead. Increasing the size of both the models and the data pools is a crucial component of many ethical concerns around using and building LLMs, which we will discuss in chapter 9.

</details>

### 1.7 LLM为何表现如此出色

我们将在后续章节讨论 LLMs 的工作原理细节，但此处也有必要分享一个从机器学习算法研究中得出的关键教训。多年来，要让自己算法针对任意任务取得更好性能，通常意味着要在算法设计上更加巧妙。你会研究问题、数据和数学，尝试推导出关于世界的有价值真理，然后将它们编码到算法中。如果做得好，性能提升，所需数据减少，一切都很美好。许多你可能听过的经典深度学习算法，如卷积神经网络（CNN）和长短期记忆网络（LSTM），从高层次看，都是人们苦思冥想和巧妙设计的结果。甚至更简单的“浅层”机器学习算法，例如不依赖神经网络或深度学习的 XGBoost，也是通过巧妙的算法设计创造出来的。

LLMs 展示了一个更近期的趋势。它们不是在算法上耍聪明，而是保持简单，实现一个仅捕捉信息之间关系的朴素算法。在很多方面，LLMs 将更少关于世界的先验信念强行嵌入到算法中。从根本上讲，这提供了更大的灵活性。如果我告诉你相反的做法才是人们改进算法的传统方式，你可能会怀疑这怎么会是个好主意？区别在于 LLMs 和类似技术只是规模更大，大得多。它们在远更多的数据上训练，并能从更多句子中捕捉更多词语间的关系；这种暴力方法在性能上似乎已经超越了经典的机器学习方法。这一思想在图 1.5 中展示。

<details>
<summary>英文原文</summary>

We discuss the details of how LLMs work in the coming chapters, but it is also worth sharing here a key lesson learned by researching ML algorithms. For many years, getting better performance from your algorithm for whatever task you were trying to do often meant getting clever about designing your algorithm. You would study your problem, the data, and the math and attempt to derive valuable truths about the world that you could then encode into your algorithm. If you did a good job, your performance improved, you required less data, and all was good in the world. Many classic deep learning algorithms you may hear about, like convolutional neural networks (CNNs) and long short-term memory (LSTM) networks, are, at a high level, the result of people thinking hard and getting clever. Even simpler “shallow” ML algorithms, such as XGBoost, that do not rely on neural networks or deep learning were created using clever algorithm design.

LLMs demonstrate a more recent trend. Instead of getting clever about the algorithm, they keep it simple and implement a naive algorithm that simply captures relationships between pieces of information. In many ways, LLMs have fewer beliefs about the world forcibly baked into the algorithm. Fundamentally, this provides more flexibility. How could this be a good idea if I told you the opposite approach was how people improved algorithms? The difference is that LLMs and similar techniques are just bigger, massively so. They are trained on far more data and with far more ability to capture more relationships between more words in more sentences; this brute-force approach appears to have outpaced classic ML methods in performance. This idea is illustrated in figure 1.5.

</details>

![图 1.5 如果算法的巧妙程度取决于编码到设计中的信息量，那么传统技术往往通过比前代更巧妙来提升性能。如圆圈大小所示，LLMs 大多选择了一种“更笨”的方法：使用更多数据和参数，并对算法可学习的内容施加最小限制。](assets/fig-1-5-afc17300a1.png)

*图 1.5 如果算法的巧妙程度取决于编码到设计中的信息量，那么传统技术往往通过比前代更巧妙来提升性能。如圆圈大小所示，LLMs 大多选择了一种“更笨”的方法：使用更多数据和参数，并对算法可学习的内容施加最小限制。*

正如我们之前所说，更大并不意味着在所有指标上都更好。这些模型目前部署起来在物流和计算方面都是一个挑战。许多现实世界中的约束，包括响应时间、功耗、电池消耗和可维护性，都受到了负面影响。所以，LLM仅仅在一个狭隘的“性能”定义上有所提升。

<details>
<summary>英文原文</summary>

As we have already stated, bigger is not better by every metric. These models are currently a logistical and computational challenge to deploy. Many real-world con-straints, including response time, power draw, battery drain, and maintainability, are all negatively affected. So it is only a narrow definition of “performance” by which LLMs have improved.

</details>

不过，“做大”胜过“取巧”这一课仍值得借鉴。有时，在设计机器学习方案时，即便用了大语言模型，最佳答案可能仍是“我们再多弄点数据吧”。

<details>
<summary>英文原文</summary>

Still, the lesson on the value of “going bigger” over “getting clever” is worth considering. Sometimes, in your design of a machine learning solution, even if you are using an LLM, the best answer may be “Let’s just go get a lot more data.”

</details>

### 1.8 LLM实战：好、坏、可怕之处

在本书中，我们将给出一些例子，展示LLM是如何失败的，通常是以滑稽或愚蠢的方式。

这些例子的目的并非是说LLM无法完成任务。通过改变输入、设置或随机运气，你往往可以让LLM表现得更好。

这些例子的目的在于向你展示LLM是如何失败的，通常是在一些简单到连孩子都能做得更好的事情上。当你阅读本书并与LLM互动时，这些例子应当让你停下来思考：“如果我用ChatGPT处理困难任务，但它连简单的任务都失败，我是不是在自找麻烦？”答案往往是一个响亮的“是”！安全使用LLM需要对输出持怀疑态度，努力验证和确认正确性，并具备相应的适应能力。如果你用一个LLM来执行你自己无法完成的任务，你就有可能暴露于你无法亲自验证的错误结果中。在整本书中，随着我们讨论如何更多地使用LLM，我们将不断将这个观点以及如何处理它融入讨论中。

很容易想象LLM在其正常工作时可能让我们的生活变得更轻松的许多方式——回复所有电子邮件、总结长文档、解释新概念。但很多人并不自然地意识到事情可能出错并迅速变得危险。

这种对抗性思维通常可以通过一个初始例子来激发：比如你想学习如何制作炸弹。如果你问ChatGPT这个问题，你会得到标准回答：“抱歉，我无法协助这个请求。如果你处于危机中或需要帮助，请联系当地当局或能够提供帮助的专业人士。”然而，研究人员最近展示了如何让ChatGPT和许多其他商业LLM毫不犹豫地回答这个问题，以及其他许多危险的信息请求[4]。

有人可能会争辩说，如果有人聪明到能想出如何欺骗LLM，他们可能也能从其他来源获得任何危险信息。这可能是对的，但同时它没有考虑到LLM和生成式AI工具的自动化规模。没有AI或ML算法是完美的，如果数百万人提问，LLM可能在0.01%的情况下产生危险回应。ChatGPT拥有超过1亿用户[5]，所以那就是10000次危险回应。当你考虑恶意行为者可能开始自动化什么时，问题就更严重了。我们将在本书的后半部分进一步讨论这个问题。

<details>
<summary>英文原文</summary>

Throughout this book, we will give examples of how LLMs can fail, often in hilarious or silly ways. The point of these illustrations isn’t to say that LLMs are incapable of performing a task. With changes to the input, setup, or random luck, you can often get LLMs to work better.

The point of such illustrations is to show you how LLMs can fail, often on things so simple that a child can do them better. As you read through this book and interact with LLMs yourself, these illustrations should give you pause and lead you to the thought, “If I use ChatGPT for a hard task, but it fails on easy ones, am I setting myself up for failure?” The answer may often be an emphatic yes! Using LLMs safely requires a degree of skepticism or doubt about the outputs, work to verify and validate correctness, and the ability to adapt accordingly. If you use an LLM for a task you cannot do yourself, you risk exposing yourself to errant results you can’t verify personally. We will continually weave this point and how to deal with it into the conversation as we discuss how to use LLMs more throughout the book. It is easy to imagine many ways that LLMs can potentially make our lives easier when it does work—answering all your emails, summarizing long documents, and explaining new concepts. What does not come naturally to many is how things can go wrong and quickly become dangerous.

This kind of adversarial thinking can often be prompted with an initial example: say you want to learn how to make a bomb. If you ask ChatGPT that question, you get the sanitized answer, “Sorry, I can’t assist with that request. If you’re in crisis or need help, please contact local authorities or professionals who can help.” However, researchers have recently shown how to get ChatGPT and many other commercial LLMs to answer the question without hesitation, among many other dangerous requests for information [4].

One might argue that if someone is so clever as to figure out how to trick the LLM, they could probably get whatever dangerous information they want from another source. This is likely true, but at the same time, it fails to account for the scale of automation in LLMs and generative AI tools. No AI or ML algorithm is perfect, and if millions of people ask questions, LLMs might produce a dangerous response 0.01% of the time. ChatGPT has over 100 million users [5], so that is 10,000 dangerous responses. The problem worsens when you consider what a malicious actor might begin to automate. We will discuss this problem further in the second half of the book.

</details>

我们期待与您一起探索LLM的工作原理。最终，您将详细了解在您的业务或日常生活中应用LLM的革命性能力时需要考虑的诸多因素。

<details>
<summary>英文原文</summary>

We look forward to your joining us in exploring how LLMs work. In the end, you’ll have a detailed understanding of many things to consider when employing LLMs’ revolutionary capabilities in your business or daily life.

</details>

### 总结

ChatGPT 是一种大型语言模型（LLM），而LLM本身属于更广泛的生成式 AI/ML 家族。生成式模型能产生新的输出，LLM 的输出质量独特，但它们的构建和使用成本极其高昂。LLM 的架构大致模仿了人类大脑功能和语言学习的不完全理解。这仅作为设计灵感，并不意味着模型具备与人类相同的能力或弱点。智能是一个多面且难以量化的概念，因此很难判断 LLM 是否具有智能。更简单的方式是从能力和可靠性的角度来思考 LLM 及其潜在用途。人类语言必须转换为 LLM 的内部表示，反之亦然。这种表示的形成方式会影响 LLM 学习的内容，并影响你如何利用 LLM 构建解决方案。

<details>
<summary>英文原文</summary>

ChatGPT is a type of large language model, which is itself in the larger family of generative AI/ML. Generative models produce new output, and LLMs are unique in the quality of their output but are extremely costly to make and use. LLMs are loosely patterned after an incomplete understanding of human brain function and language learning. This is used as inspiration in design, but it does not mean the models have the same abilities or weaknesses as humans. Intelligence is a multifaceted and hard-to-quantify concept, making it difficult to say whether LLMs are intelligent. It is easier to think about LLMs and their potential use in terms of capabilities and reliability. Human language must be converted to and from an LLM’s internal representa-tion. How this representation is formed will change what an LLM learns and influence how you can build solutions using LLMs.

</details>



---

<a id="ch2"></a>

## 第 2 章: 分词器：大语言模型如何看待世界

2 分词器：大型语言模型如何看世界

<details>
<summary>英文原文</summary>

2 Tokenizers: How large language models see the world

</details>

如第1章所述，在人工智能领域，借鉴人类学习的类比往往有助于解释机器如何“学习”。你阅读和理解句子的方式是一个复杂的过程，会随着年龄增长而变化，并涉及多个序列性和并发性的认知过程[1]。然而，大语言模型（LLM）使用比人类认知过程更简单的过程。它们采用基于神经网络的算法，从大量数据中捕捉词语之间的关系，然后利用这些关系信息来理解和生成句子。

我们关于这些算法如何工作的讨论将从其输入开始：文本句子。在本章中，我们将探讨LLM如何处理这些句子，使其成为模型的输入。正如语言对你思考和处理信息至关重要一样，LLM的输入对于影响LLM能够执行的概念和任务类型也至关重要。

<details>
<summary>英文原文</summary>

As discussed in chapter 1, in the world of artificial intelligence, it is often helpful to find analogies to human learning to explain how machines “learn.” How you read and understand sentences is a complex process that changes as you get older and involves multiple sequential and concurrent cognitive processes [1]. Large language models (LLMs), however, use simpler processes than human cognitive processes. They em-ploy algorithms based on neural networks to capture the relationships between words in large amounts of data and then use this information about relationships to interpret and generate sentences.

Our discussion of how these algorithms work will begin with their input: sentences of text. In this chapter, we explore how the LLM processes these sentences to become inputs for the model. Just as language is critical for how you think and process information, the inputs to an LLM are crucial in influencing what kinds of concepts and tasks LLMs can perform.

</details>

### 2.1 词元：数值表示

大语言模型处理句子似乎是显而易见的，但要真正理解，我们必须更具体一些。在讨论大语言模型工作原理时，你会看到文本句子对驱动大语言模型的神经网络算法而言并不自然，因为神经网络本质上是用数字来完成工作的。如图2.1所示，大语言模型所用的算法必须先将人类文本转换为数字表示，然后才能进行处理。词元就是大语言模型用来将文本拆分为可编码为数字的片段所用的表示。

<details>
<summary>英文原文</summary>

It may seem obvious that LLMs should process sentences, but to fully understand, we must be more specific. As we talk about how LLMs work, you will see that textual sentences are unnatural for the neural network algorithms that power LLMs because neural networks fundamentally employ numbers to do their work. As shown in figure 2.1, the algorithms employed by LLMs must convert human text into a numeric representation before working with it. Tokens are the representations that LLMs use to break text into pieces that can be encoded as numbers.

</details>

![图 2.1 为了理解文本，LLM必须将文本拆分为词元。每个唯一的词元都有一个与之关联的数字标识符。](assets/fig-2-1-1da6a91561.png)

*图2.1 为了理解文本，LLM必须将文本拆分为词元。每个唯一的词元都有一个与之关联的数字标识符。*

你可以将词元视为大语言模型处理文本的最小单元——不妨称之为“原子”，即构成一切其他事物的最小部分。那么，文本的原子是什么呢？思考一下：当你阅读本书时，你的大脑用来处理意义的最小构建块是什么？两个显而易见的答案是字母和单词。我们很容易将字母定义为原子，因为单词由字母组成，但你是否真的有意识地阅读每个单词中的每个字母？对大多数人来说，答案是否定的。（如果你是本书合著者之一那样的阅读障碍者，这个问题就显得古怪。但认知处理过程复杂且尚未完全理解，请暂且容忍我们的类比！）你关注的是更显眼的单词和词缀部分。事实上，即便我们故意用错拼写或字母，你大概仍能理解这句话。人类在阅读时会无意识地利用词缀等部分来处理文本，而大语言模型的构建也遵循同样的原理。

在本章中，你将学习文本如何转换为词元的过程。首先，我们将更详细地讨论词元；然后，讨论用于决定句子如何转换为词元的程序。

<details>
<summary>英文原文</summary>

You can think of tokens as the smallest unit of text an LLM processes—an “atom,” if you will, the smallest part from which all other things are built. So what are the atoms of text? Consider this: As you read this book, what are the smallest building blocks that your brain uses to process meaning? Two natural answers are letters and words. It is very tempting to define letters as the atom since words are made of letters, but do you consciously read every letter in every word? For most people, the answer is “no.” (If you are dyslexic like one of the co-authors of this book, this is a bizarre question. But cognitive processing is complex and not fully understood; please bear with us on the analogies!) You look at the more prominent words and word parts. In fbct, yoy cn probbly unrestand ths sentnce ever through we diddt sue th ryght cpellng or l3ttrs. People unconsciously use parts of words to process text, and LLMs are built using the same principle.

In this chapter, you will learn how the process of converting text to tokens works. First, we will discuss tokens in more detail; then, we will discuss the procedures used to decide how sentences are turned into tokens.

</details>

### 2.2 语言模型只看得到标记

到成年时，大多数说英语的人掌握约3万个单词[2]。GPT-3，最初驱动ChatGPT的大语言模型，拥有50,257个token的词汇量[3]。这些token不是单词，而是被称为子词的部分，是一种介于单词和字母之间的表示。直观来说，token捕捉的是语言中最小的有意义语义单元。例如，schoolhouse通常会被拆分为school和house两个token，而thoughtful则被拆分为thought和ful。这有助于识别常见词汇，并利用子词理解从未见过的新词。人们常常使用类似的技巧，称为语义分解，来理解从未见过的单词。我们凭直觉将新词拆解成组成部分，基于已理解的词汇来把握其含义。

特征工程是将数据转换为更适合算法和任务所需形式的过程。要构建能检测文本语言的算法，你可以编写代码，以文本为输入，输出每个字符出现的百分比。例如，如果文档中é出现很多，你就有一个很好的特征表明该文档更可能是西班牙语或法语，而非俄语或中文。可靠的特征工程需要考虑模型的工作原理、你想要实现的目标，以及如何为模型与目标的组合准备数据。

分词是大语言模型的特征工程；它至关重要，因为token是模型交互的唯一信息。token被视为独立的抽象事物，本质上互不关联。它们之间的关系通过数据观察来学习。

回顾图2.1，显然Dis和dis的token是相关的，唯一区别在于一个以大写D开头。然而，你可以看到模型将标识符4944分配给Dis，将标识符834分配给dis。也就是说，模型本质上不认为表示Dis和dis的token之间存在任何联系，尽管我们人类能看出明显关联。模型甚至看不到Dis或dis。为了让大语言模型处理token，我们必须将这些token转换为数字，这样模型看到的就是数字4944和834。重要的是，模型没有任何直接方式知道这些token是相关的。

token是从子词到唯一数字表示的映射。相应地，分词是将整个文本字符串转换为token序列的过程。如果你之前使用过机器学习库（尤其是自然语言处理工具），可能对某些简单的分词形式比较熟悉。例如，简单的分词过程通过按空格拆分文本，将文本分解为token。然而，这种方法限制了创建子词的能力，也不适用于不按空格分隔单词的语言，如中文。

<details>
<summary>英文原文</summary>

By adulthood, most English-speaking people know around 30,000 words [2]. GPT-3, the LLM that initially powered ChatGPT, has a vocabulary of 50,257 tokens [3]. These tokens are not words but parts of words referred to as subwords, a representation that is somewhere between words and letters. Intuitively, a token captures language’s minimum meaningful semantic unit. For example, the word schoolhouse will often get broken into two tokens, school and house, and the word thoughtful as thought and ful. This is useful for recognizing frequent words and having the subwords to interpret new words we have never seen before. People often use a similar technique, called semantic decomposition, to understand words they’ve never seen before. We intuitively break new words into constituent parts to grasp their meaning based on words we already understand.

Feature engineering is the process of converting your data to a form that is more convenient to your algorithm and the task you want to solve. To build an algorithm that can detect the language of a given text, you could write code that takes text as input and outputs the percentage of times each character occurs. For example, if é appears a lot in a document, you have a good feature to indicate that the document is more likely to be Spanish or French than Russian or Chinese. Sound feature engineering is concerned with thinking through how your model works, what you want to achieve, and how to prepare your data for the combination of model and goal.

Tokenization is the feature engineering of LLMs; it is critically essential because tokens are the only information a model interacts with. Tokens are seen as individual, abstract things that are not inherently connected. The relationships are learned through observation of data.

Looking back at figure 2.1, it is evident that the tokens for Dis and dis are related, the only difference being that one starts with a capital D. However, you can see that the model assigns the identifier 4944 to Dis and the identifier 834 to dis. That is, the model doesn’t inherently see any connection between the tokens representing Dis and dis, even if we, as humans, see an obvious connection. The model doesn’t even see Dis or dis. For an LLM to process tokens, we must convert those tokens into numbers so that the model will see the numbers 4944 and 834. Importantly, the model doesn’t have any direct way to know that these tokens are related. A token is a mapping from a subword to a unique numeric representation. In turn, tokenization is the process of converting a full-text string into a sequence of tokens. If you have used machine learning libraries before (especially any natural language processing [NLP] tools), you are probably familiar with some of the simpler forms of tokenization. For example, a simple tokenization process breaks a text into tokens by splitting a text based on spaces. However, this approach limits our abilities to create subwords or process languages that don’t use whitespace to delimit words, such as Chinese.

</details>

### 2.2.1 分词过程

分词遵循的通用过程如图2.2所示，包含四个关键步骤：

<details>
<summary>英文原文</summary>

The generic process that tokenization follows is shown in figure 2.2 with four key steps:

</details>

1. 接收待处理的文本——这意味着从用户、互联网或任何包含所需文本的源获取字符串数据类型的文本输入（由字母、数字或符号组成的集合）。

2. 转换字符串——这通常涉及以某种有用的方式更改字符串，例如将大写字符转换为小写。这一步骤也可能出于安全原因（例如，文本来自用户，我们需要删除任何可能看起来像恶意输入的内容）或为了消除文本中的无关变化以帮助算法更好地学习。这个过程称为规范化。

3. 将字符串分解为词元——一旦有了字符串，就需要将其分割成一系列离散的子字符串；这些就是大字符串中的词元。这称为分词。

4. 将每个令牌映射到唯一标识符——唯一标识符通常是一个整数，生成LLM能够理解的输出。

<details>
<summary>英文原文</summary>

1 Receiving the text to process—This means obtaining text input as a string data type (a collection of letters, digits, or symbols) from a user, the internet, or whatever source that has the text you want.

2 Transforming the string—This often involves changing the string in some useful way, such as converting uppercase characters into lowercase. This could also be done for security reasons (e.g., the text came from a user, and we need to remove anything that might look like some malicious input) or to eliminate irrelevant variations in the text to help the algorithm learn better. This process is known as normalization.

3 Breaking the string into tokens—Once a string is available, it needs to be separated into a sequence of discrete substrings; these are the tokens found in the larger string. This is referred to as segmentation.

4 Mapping each token to a unique identifier—The unique identifier is usually an integer number, which produces output that the LLM can understand.

</details>

![图 2.2 通常，分词涉及处理输入以生成令牌的数字标识符。](assets/fig-2-2-1353cf4e5f.png)

*图2.2 通常，分词涉及处理输入以生成令牌的数字标识符。*

这个过程的首尾部分几乎没有选择或不同行为的余地。首先，你需要输入待处理的内容；最后，你需要为每个 token 分配一个数值标识符，以便存储和检索你将与该 token 关联的信息。中间的两个步骤——规范化（normalization）和切分（segmentation）——才是你可以选择如何处理的地方。

<details>
<summary>英文原文</summary>

The first and last parts of this process have little room for choice or different behavior. First, you need input to process; last, you need a numeric identifier for each token to store and retrieve the information you will associate with that token. The two middle steps, normalization and segmentation, are where you can choose what happens.

</details>

分词流程的最后一步是构建词汇表。模型的词汇表是指训练过程中，当我们向算法提供学习数据时，所看到的不同token的总数。要构建一个包含大量不同token的丰富词汇表，几乎总是需要大量的数据。

为模型选择词汇表涉及一系列权衡：词汇表越大，模型能成功处理的信息就越多。想象一个一岁大的孩子，词汇量可能只有几十个单词。这个孩子不会是一个很有效的沟通者（但这没关系；他们有大量的时间去学习）。因此，更大的词汇表不仅有助于模型理解更多事物，也会让模型变得更大。如果词汇表过大，可能会因为使用它所需的计算量而让模型变慢，或者模型可能消耗过多的内存或磁盘存储，这使得将其传输或共享到其他机器变得更加困难——例如，在将其作为软件应用的一部分进行部署时。

通过处理训练数据并识别token来构建模型的词汇表。每次遇到一个新token时，你都会根据已看到的唯一token数量给它分配一个唯一的标识符。这个过程通常很简单，只需将一个计数器初始化为0，每发现一个新token就将其递增。一旦这个过程完成，你就得到了一个本质上相当于编码器的分词器。分词器可以接收文本作为输入，并返回该文本的数字编码，供LLM算法作为其输出使用。

<details>
<summary>英文原文</summary>

The last step of the tokenization process is where the vocabulary is built. The vocabulary of a model is the total number of unique tokens that are seen during training when we give the algorithm data to learn from. It almost always takes a large amount of data to build a rich vocabulary with many unique tokens. Choosing the vocabulary for a model involves a series of trade-offs: the larger the vocabulary, the more information your model can process successfully. Consider a one-year-old child with a vocabulary of maybe a few dozen words. This child will not be a very effective communicator (but that’s okay; they have lots of time to learn). So a more extensive vocabulary not only helps the model understand more things, but it also makes the model larger. If you have a vocabulary that’s too large, you may make the model slower due to the number of computations required to use it, or the model may consume an excessive amount of memory or disk storage, which makes it more difficult to transfer or share to other machines—for example, when deploying it as a part of a software application.

You build the model’s vocabulary by processing the training data and identifying tokens. Each time you see a new token, you give it a unique identifier based on the number of unique tokens you’ve seen. This process is often as simple as storing a counter set to 0 and incrementing it every time a new token is found. Once the process is complete, you have a tokenizer that is effectively an encoder. The tokenizer can receive text as input and return a numeric encoding of that text that the LLM algorithms can use as its output.

</details>

### 2.2.2 控制分词中的词汇量

GPT-NeoX 是一个公开可用的大型语言模型，其词汇表在磁盘上占用约 10 GB 空间。

这些数据量巨大，已经足以使许多实际应用场景在数据存储和计算方面面临挑战。

词汇表如此之大，以至于将其存储在 micro-SD 卡上会慢得无法接受，从而使得在手机或某些游戏机上的使用成为重大挑战。

它足够大，无法实时流式传输，必须下载并加载到处理器的 RAM 中才能执行分词。

然而，词汇表必须足够大，以表示模型在训练和使用过程中可能遇到的所有单词和子词。

假设模型遇到一个不在其词汇表中的单词，且无法通过组合词汇表中的子词来表示。

在这种情况下，模型无法捕捉到该段文本的信息。

因此，有必要在词汇表大小的担忧与模型解释广泛内容的需求之间进行权衡。

在 NLP 中，这通常被称为“词汇外问题”，即遇到无法用模型可用 token 表示的单词。

词汇表大小是影响 LLM 规模的因素之一，因此讨论控制词汇表大小的方法和权衡至关重要。

在本节中，我们将描述改变分词过程的行为如何影响词汇表大小，进而影响模型的能力和准确性。

<details>
<summary>英文原文</summary>

GPT-NeoX, a publicly available LLM, takes about 10 GB to store its vocabulary on disk. That is a lot of data, already large enough to make many real-world use cases challenging from the perspective of data storage and computation. It is so large that storing it on a micro-SD card would be prohibitively slow, making use on a mobile phone or some game consoles a significant challenge. It is big enough that it can’t be streamed in real time and must be downloaded and loaded into the processor’s RAM to perform tokenization. However, a vocabulary must be sufficiently large to represent all words and subwords the model will encounter during training and use. Suppose a model encounters a word that is not in its vocabulary and cannot be represented by combining subwords in its vocabulary. In that case, the model cannot capture information about that piece of text. As a result, it is essential to weigh concerns about vocabulary size against the need for models to interpret a wide variety of content. In NLP, this is often called the out-of-vocabulary problem, when we encounter words we can’t represent using the tokens available to the model. Vocabulary size is one factor contributing to an LLM’s size, so discussing methods and tradeoffs for controlling vocabulary size is vital. In this section, we will describe how changing the tokenization process’s behavior can influence vocabulary size and affect model capabilities and accuracy.

</details>

![图 2.3 归一化过程通常涉及将文本中的大写字符转换为小写并移除标点符号。](assets/fig-2-3-4971a990f1.png)

*图2.3 归一化过程通常涉及将文本中的大写字符转换为小写并移除标点符号。*

图2.3聚焦于第二个转换步骤——归一化，它将大写字母“H”和“W”转换为小写，并移除标点。这些常见的归一化步骤源于经典NLP流程，在当今的深度学习方法中有时仍被沿用。它们能立即带来词汇量缩减的效果，不再需要将“Hello”和“hello”表示为两个独立的token，而是映射为一个唯一token。这种映射意义重大，因为每个句首大写的词都会在词汇表中额外增加一个大写版本。归一化还有助于处理各种打字错误和拼写错误。

例如，在撰写本书时，我们曾输入“LLMs”、“LLms”和“llms”，并出现其他多种大小写混用的笔误。将每个变体中的字符全部转为小写，就能将所有这类笔误归一为一种简洁形式，从而得到更小的词汇表并减少歧义。然而，将文本全部转为小写并不总能减少歧义。以“Bill”和“bill”为例：前者意指人名，后者更可能指货币单位（或其他定义）。大小写不仅对理解文本含义至关重要，也有助于理解文本中的错误。再回想一下我们在本书中多种错误大小写“LLMs”的方式。高质量的AI算法应能识别出我们的笔误并予以纠正！ChatGPT具备这种能力，因此模型需要保留大小写信息。因此，在词汇表大小与潜在模型准确率之间存在一个重要的权衡。

在经典NLP乃至不算太古老的深度学习模型（如BERT，ChatGPT强大LLM的前辈）中，算法识别并修正笔误的能力极其有限，除非专门为此设计解决方案。因此，过去用于构建稳健归一化步骤的大量工作，如今在LLM中已被抛弃。更庞大的词汇表更受欢迎，以便训练出能够理解错误、更强大的模型。

<details>
<summary>英文原文</summary>

In figure 2.3, we focus on the second transformation step, normalization, which converts the uppercase characters “H” and “W” to lowercase and removes punctu-ation. These common normalization steps originate from classical NLP pipelines and are still sometimes done in modern deep learning approaches today. They have the immediately desirable effect of reducing the size of the vocabulary. Instead of needing to represent “Hello” and “hello” as two separate tokens, they get mapped to one unique token. This mapping makes an enormous difference because every word that starts a sentence and gets capitalized would potentially duplicate a word in the vocabulary with a capitalized version. Such normalization can also help with various typos and misspellings.

For example, while writing this book, we typed “LLMs,” “LLms,” and “llms,” and made various other mixed-case typos. Converting each character to lowercase in each variation resolves all these typos into a single, simple form, so we get a smaller vocabulary and decrease ambiguity.

However, converting text to lowercase doesn’t always decrease ambiguity. Consider “Bill” and “bill.” In the first situation, capitalization is vital for understanding that “Bill” is probably someone’s name, and “bill” is more likely a unit of money (or one of the other definitions of “bill”). Capitalization is crucial not only for understanding the meaning of the text but also for understanding the errors in the text. Consider again all the various ways we miscapitalized “LLMs” in this book. A high-quality AI algorithm would be able to recognize that we made a typo and correct it! ChatGPT is capable of this and thus requires capitalization in the model. So there is an important tradeoff between vocabulary size and potential model accuracy to consider. In classical NLP and even not-that-old deep learning models like BERT (a prede-cessor to the LLMs that power ChatGPT), the ability of an algorithm to recognize typos and fix them was extremely limited outside of solutions designed explicitly for that purpose. For this reason, much of the work that used to go into engineering a robust normalization step has been discarded for LLMs today. A more extensive vocabulary is desirable to produce more capable models that can learn to understand mistakes.

</details>

### 2.2.3 分词详解

分词过程中的标准化与分割步骤在很大程度上决定了词汇表的大小。

图2.4展示了一种最直接的分词策略。

该策略遵循一个简单规则：每当文本中出现空格时，就将字符串分割为对应的词元。

以"hello world"为例，相当于在Python中调用`"hello world".split(" ")`。

这是一种合理的方法，正如我们人类阅读句子的方式。

但这也带来了一些微妙的复杂性。

<details>
<summary>英文原文</summary>

The normalization and segmentation steps in the tokenization process largely determine the vocabulary size. In figure 2.4, we show one of the most straightforward strategies for tokenization. This strategy follows a simple rule: any time a space is seen in the text, split the larger string into those tokens. In the case of “hello world,” it is as easy as calling "hello world".split(" ") in Python. This is a reasonable approach to take; it is how we, as humans, read sentences. But it also adds some subtle complexity.

</details>

![图 2.4 分词过程将规范化后的文本分割成单词或词元，以便每个都能独立处理。](assets/fig-2-4-f3ba3f9e0b.png)

*图2.4 分词过程将规范化后的文本分割成单词或词元，以便每个都能独立处理。*

文本中出现标点符号会怎样？如果我们使用空格规则将字符串“hello, world”转换为["hello,", "world"]，就会遇到与大小写类似的问题。我们最终得到了代表同一概念的两个不同token："hello"和"hello,"。传统方法通常通过移除标点或制定更复杂的字符串拆分规则来解决这个问题。虽然这在减少词汇量方面是正确的一步，但手动指定分词规则并不能解决其他问题。例如，对于像中文这样不使用空格分隔单词的语言，基于规则的分词策略会遇到很大困难。

<details>
<summary>英文原文</summary>

What happens when you have punctuation in your text? If we use our white space rule to convert the string “hello, world” into ["hello,", "world"], we run into a similar problem as we do with capitalization. We end up with two distinct tokens for the same concept: "hello" and "hello,". The old-school approach often addressed this by removing and developing more complex rules for splitting strings into tokens. While this is a step in the right direction toward reducing vocabulary size, manually specifying tokenization rules does not address other concerns. For example, rule-based tokenization strategies are a significant struggle for languages like Chinese that do not use spaces to separate words.

</details>

### 用字节对编码识别子词

LLM的总体思路是减少人工特征工程，让算法承担繁重工作。因此，通常使用一种称为字节对编码（BPE）的算法将字符串拆分为token。字节对编码是一种将单词拆分为常见字符子词序列的算法。如今的BPE通常使用自定义分词器，几乎不进行规范化处理。

<details>
<summary>英文原文</summary>

The general theme of LLMs is to do less feature engineering by hand and let algori-thms do the heavy lifting instead. For this reason, an algorithm known as byte pair encoding (BPE) is typically used to break strings into tokens. Byte pair encoding is an algorithm for breaking words into common subword sequences of characters. BPE today is usually done with a custom segmenter and almost no normalization.

</details>

注：通过实验，我们发现许多类似ChatGPT的产品会移除一些不打印的Unicode字符（Unicode确实很奇特），但除此之外基本上会原样保留你的文本。

大多数先前的语言模型确实使用了多种方式的规范化，而如何更好地为大语言模型规范化文本，我们认为是一个值得探讨的开放问题。

<details>
<summary>英文原文</summary>

NOTE By experimentation, we see many ChatGPT-like products will remove some Unicode characters that do not print (Unicode is weird), but otherwise mostly take your text as-is. Most prior language models do use various flavors of normalization, and how to normalize text for LLMs better is, we think, a good and open question.

</details>

由于寻找最高效的子词集合计算成本过高，BPE 采用一种启发式方法来简化。该算法先将单个字母视为词元，然后找出出现最频繁的相邻字母对，并将其合并为子词词元。算法重复此过程多次，持续处理子词词元，直到达到某个阈值，词汇表“足够小”为止。例如，在第一轮中，BPE 算法统计英语中单个字母的出现频率，发现字母“i”、“n”和“g”经常彼此相邻出现。在第一轮中，BPE 可能观察到“n”和“g”一起出现的频率高于“i”和“n”，因此它将生成词元“i”和“ng”。在后一轮中，它可能根据该字母组合的出现频率与“ng”与其他字母或子词共现的频率对比，将这些词元合并为“ing”。一旦 BPE 达到停止点，它将识别出诸如“eating”和“drinking”等单个单词作为频繁出现的组合。它还可能将“ing”捕获为后缀，以便以该子词结尾的其他单词也能表示为词元。算法完成后，我们得到的词元中，有些捕获完整单词，有些则捕获子词。图 2.5 从高层概述了这一过程。

<details>
<summary>英文原文</summary>

Since finding the most efficient set of subwords is a computationally expensive task, BPE uses a heuristic to take a shortcut. It starts by looking at individual letters as tokens and then finds pairs of adjacent letters that occur most frequently and combines them into subword tokens. The algorithm repeats this process many times, continuing with subword tokens, until some threshold is met and the vocabulary is “small enough.” For example, in the first pass, the BPE algorithm examines the frequency of the individual letters used in English and encounters the letters “i,” “n,” and “g” near each other frequently. In the first pass, BPE might observe that “n” and “g” occur together more frequently than “i” and “n,” so it will produce the tokens i and ng. In a subsequent pass, it may combine those tokens into ing based on the frequency of that combination of letters versus how often “ng” occurs with other letters or subwords. Once BPE has reached its stopping point, it will have identified individual words such as “eating” and “drinking” as frequently occurring combinations. It may also capture “ing” as a suffix so that other words ending with that subword can also be represented as tokens. When the algorithm is complete, we end up with tokens that capture complete words and others that capture subwords. This process is shown at a high level in figure 2.5.

</details>

![图 2.5 简化的字节对编码算法用于创建词元：首先，找到最频繁的字符对“ng”。然后，将所有“ng”替换为占位词元“T”，并将“ng”添加到词汇表中。重复这个过程，直到没有常见的字节对为止。](assets/fig-2-5-b804ac34a1.png)

*图2.5 简化的字节对编码算法用于创建词元：首先，找到最频繁的字符对“ng”。然后，将所有“ng”替换为占位词元“T”，并将“ng”添加到词汇表中。重复这个过程，直到没有常见的字节对为止。*

注意：运行BPE算法创建词汇表的成本惊人地高，因为它必须多次读取输入数据以计算最常见的字母组合。

尽管大语言模型在超过5亿甚至10亿页的文本上进行训练，但其分词器通常只使用这些数据的一小部分来创建。通常，分词器是在一个规模小得多（大约一本小说大小）的文本集合上训练的。

<details>
<summary>英文原文</summary>

NOTE Running the BPE algorithm to create a vocabulary is surprisingly expensive because it must read the input data many times to calculate the most frequent combinations of letters. While LLMs are trained on over 500 million or even 1 billion pages of text, their tokenizers are usually created using a tiny subset of that data. Often, a tokenizer is trained using a much smaller collection of text the size of a novel.

</details>

BPE流程初看可能有些奇怪，但它本质上是一种识别语料库中常见字符串的方法。例如，BPE几乎总会学会将“New York”表示为一个token，这很有用，因为纽约州和纽约市在文本中频繁出现。将整个概念表示为一个token，使得利用这类信息变得更加容易。实际上，大多数常见词都会成为独特的token，而罕见词则有望通过子词组合来捕获。例如，“loquacious”会被GPT-4分词为“lo”、“qu”和“acious”。这种方法之所以成功，是因为“acious”是表示倾向/习性的拉丁后缀，有助于模型正确处理不常见的词汇。但它也是一个失败案例，因为拉丁前缀“loqu”被拆成了两个token而非一个，增加了学习难度。

BPE构建词汇表后，模型作者会出于各种原因手动添加额外token，例如某些特定知识领域中的重要词汇。正如我们将在下一节讨论的，在某些领域，拥有正确的token通过捕获细微含义能产生显著效果。因此，作者通常会确保包含必要的token。模型作者还会添加特殊token，它们不直接表示单词部分，而是为模型提供辅助信息。常见的例子包括“未知”token（通常表示为[UNK]），用于分词器无法正确处理某个符号时；以及系统token [SYSTM]，用于区分模型内置提示和用户输入数据，以及其他类型的样式标记。接受文本和图像输入的多模态模型使用独特的token来告诉模型输入流何时在表示文本数据的字节和表示图像数据的字节之间切换。

OpenAI在开发ChatGPT时决定使用BPE将文本编码为token，并将其分词器作为开源包`tiktoken`发布（https://github.com/openai/tiktoken）。不过，还有其他几种自动生成token的算法和实现可用，包括Google开发的WordPiece和SentencePiece算法[4]。每种方法都有不同的权衡。例如，WordPiece在构建分词器词汇表时使用不同的技术来统计候选子词的频率。SentencePiece中实现的算法之一会处理整个句子，在计算token时保留空格，这可能有助于改善处理多种语言的模型的输出。然而，BPE是使用最广泛的算法。例如，Google最近的LLM中已完全采用BPE。无论选择哪种算法，分词器词汇表的大小都是一个关键模型参数，由负责训练和扩充分词器的数据科学家或工程师决定。

接下来的章节将深入探讨词汇表大小的一些考量以及分词器开发过程中做出的其他决策。

<details>
<summary>英文原文</summary>

The BPE process may seem odd at first, but you can think of it as a way of identifying common strings in a corpus. For example, BPE will almost always learn to represent New York as one token, which is useful since the state and city of New York are frequent occurrences in the text. Representing the whole concept as a single token makes it easier to use that kind of information. Indeed, most common words will become unique tokens, while rare words are hopefully captured as a combination of subwords. For example, loquacious will be tokenized by GPT-4 as lo, qu, and acious. This method is a success because “acious” is a Latin postfix for inclination/propensity, making it easier for the model to handle an unusual word correctly. It is also a failure case because the Latin prefix “loqu” got broken up into two tokens instead of one, making learning harder.

After BPE is used to make a vocabulary, model authors manually add additional tokens for various reasons, such as words that are important to a specific knowledge domain. As we will discuss in the next section, in some domains, having the correct tokens has a significant effect by capturing nuanced meaning. So often, the authors will make sure the necessary tokens are included. Model authors will also add special tokens that don’t directly represent word parts but provide auxiliary information to the model. Some common examples of this are the “unknown” token (typically represented as [UNK]), which is used if the tokenizer fails to process a symbol correctly, and the system token [SYSTM], which is used to distinguish between a model’s built-in prompt and user-entered data, as well as other kinds of stylistic markers. Multimodal models that accept text and image inputs use unique tokens to tell the model when the input stream switches between bytes that represent text data and bytes that represent image data.

Open AI decided to use BPE to encode text into tokens when they developed ChatGPT and have released their tokenizer as the open source package tiktoken (https://github.com/openai/tiktoken). Still, several other algorithms and implemen-tations for automatically generating tokens are available, including the WordPiece and SentencePiece algorithms developed at Google [4]. Each of these have diffe-rent tradeoffs. For example, WordPiece uses a different technique for counting the frequency of the candidate subwords when building the tokenizer’s vocabulary. One of the algorithms implemented in SentencePiece processes entire sentences, preserving white space when calculating tokens, which may improve output when building models that handle multiple languages. However, BPE is the most broadly used algorithm. For example, it is now used exclusively in Google’s recent LLMs. Regardless of the algorithm chosen, the size of a tokenizer’s vocabulary is a critical model parameter determined by the data scientist or engineer in charge of training and augmenting the tokenizer. The following sections dive deep into some of the considerations on vocabulary size and other decisions made throughout the tokenizer development process.

</details>

### 2.2.4 分词的风险

正如第1章所述，本书不会过多涉及编码。目标是对LLM的工作原理给出合理的理解，并揭开其魔法和神秘的面纱，以便你专注于思考如何将LLM应用于自己的工作。

词元化是拼图的第一块。它是一种简单但有效的策略，用于生成LLM的输入。你已经了解了词汇量大小在模型可部署性中的重要作用、在识别细微差别与构建词汇带来的不必要冗余之间的权衡、词元化过程如何影响词汇量大小，以及如何通过BPE自动进行词元选择。

在词元化时刻所做的选择影响着LLM当前和未来的能力。这些选择涉及一些宏观挑战需要留意。为了进一步探讨这个主题，BPE的两个显著但微妙的细节值得关注：句子长度与词元数量之间的关系，以及LLM可能被外观相同但二进制编码不同的字符（即同形字）所混淆。

<details>
<summary>英文原文</summary>

As mentioned in chapter 1, we won’t go much into coding in this book. The goal is to give you a reasonable understanding of how LLMs work and remove some of the magic and mystery so you can focus instead on how LLMs may be used for your job. Tokenization is the first piece of the puzzle. It is a simple but effective strategy to produce the inputs to LLMs. You have learned how the size of the vocabulary plays a significant role in a model’s deployability, the tradeoff in recognizing nuance versus the unnecessary redundancy associated with making a vocabulary, how the tokenization process influences the size of the vocabulary, and how the token selection process can be automated with BPE. The choices made at tokenization time affect what LLMs can do today and will affect them in the future. These choices involve a few big-picture challenges to be aware of. To explore this topic further, two salient yet nuanced details of BPE are worth sharing some concerns about: the relationship between sentence length and token counts and the potential for LLMs to be confused by characters, known as homoglyphs, that appear identical yet have different binary encodings.

</details>

### 更长的句子并不意味着更多的词元

BPE的一个反直觉之处在于，更长的句子并不意味着更多的token。要理解原因，请看图2.6，其中展示了GPT-3对两个不同字符串的实际分词结果。字符串“I’m running”比“I’m runnin”多一个字符，但token数却少一个！如果你不信，可以访问https://platform.openai.com/tokenizer亲自尝试对不同字符串进行分词。

<details>
<summary>英文原文</summary>

An unintuitive aspect of BPE is that longer sentences do not mean more tokens. To see why, look at figure 2.6, where we show a real tokenization of two different strings by GPT-3. The string “I’m running” is longer by one character than the string “I’m runnin,” but it is one token shorter! If you don’t believe it, you can try tokenizing different strings at https://platform.openai.com/tokenizer.

</details>

![图 2.6 对两个不同句子进行分词](assets/fig-2-6-d66d8560d4.png)

*图 2.6 对两个不同句子进行分词*

这种差异的产生是因为BPE贪心地寻找任意输入的最小token集。在这个具体例子中，字符串"running"在训练数据中出现足够频繁，因此它拥有自己的token。而在缺少"g"的情况下，词汇表中没有"runnin"这个token，因为该变体在训练数据中很少出现。因此，"runnin"需要被拆分为至少两个token，即run和nin。

分词器实现的这一细微差别是软件缺陷的温床。不同的分词器可能对同一字符串给出不同的分词结果。在设计单元测试和基础设施时，必须牢记这一因素，以避免在升级或切换分词器实现时因token生成出现新差异而感到困惑或迷失。它还会影响LLM的评估，因为许多模型对添加的空格高度敏感，而不一致的分词可能无意中导致比较结果失去公平性。

<details>
<summary>英文原文</summary>

This discrepancy occurs because BPE is greedily looking for the smallest set of tokens for any piece of input. In this specific case, the string “running” occurs frequently enough in our training data that it gets its own token. In the case where the “g” is missing, there is no token for “runnin” in our vocabulary because that variation may have appeared rarely in our training data. Thus, “runnin” needs to be broken into at least two tokens, giving us run and nin.

This nuance of tokenizer implementation is fertile ground for software bugs. Different tokenizers may provide different answers on how to tokenize the same string. When designing unit tests and infrastructure, this factor is important to keep in mind to avoid getting lost or confused when upgrading or converting between tokenizer implementations that may cause new differences in token generation. It can also affect evaluations of LLMs, as many models are highly sensitive to added white space, and inconsistent tokenization may inadvertently lead to comparisons not being apples to apples.

</details>

### 同形字造成混淆

同形字是开发者在处理多语言或考虑外部数据安全时可能遇到的问题。当输入来自任意用户时，有时可能怀有恶意，试图诱使模型做出不良行为。针对LLM的一种攻击方式就是同形字攻击。

同形字是指两个或多个字符字节编码不同，但在屏幕上渲染时外观相同。例如，拉丁字母“H”（用于大多数西欧语言）和西里尔字母“H”（用于东欧和中亚）。

BPE会将使用不同字节编码的同形字编码为不同的词元。因此，同形字会增加文本中的词元数量，改变LLM对信息的解析方式，并增加计算成本。一个有趣的同形字例子是Unicode字符U+200B，也称为“零宽空格”。该字符用于排版，占据空间，但不会打印任何内容，不会显示任何东西，也不会改变文档的渲染方式。

零宽空格是Unicode规范中许多奇特有趣的东西之一，可能被用来给你制造麻烦。因此，许多服务采用标准化步骤，移除这些奇怪字符，并将同形字替换为规范表示（例如，任何看起来像“a”的字符都必须编码为a）。例如，OpenAI当前的词元化接口会移除同形字。如果你想在自有硬件或用户设备上部署LLM，必须考虑同形字问题。

<details>
<summary>英文原文</summary>

Homoglyphs are a problem developers may encounter when working with multiple human languages or considering the security implications of processing externally provided data. When input comes from arbitrary users, sometimes it may be nefarious and want to trick your model into bad behavior. One way that could be done against an LLM is with a homoglyph attack. A homoglyph is when two or more characters have different byte encodings but appear identical when rendered on the screen. One example is the Latin letter “H” used in most Western European languages and the Cyrillic “H” used throughout Eastern Europe and Central Asia. BPE will encode homoglyphs that use different byte encodings into different tokens. As a result, homoglyphs can inflate the number of tokens in a text, change how an LLM parses the information, and run up your compute costs. An amusing example of a homoglyph is the Unicode character U+200B, also known as the “zero width space.” This character is used in typesetting and takes up space, but it does not print anything, show anything, or change anything about how a document is rendered. The zero width space is one of many strange and interesting things that exist within the Unicode specification and could be used to cause you pain. Many services thus employ normalization steps that remove such strange characters and replace homoglyphs with a canonical representation (i.e., anything that looks like an “a” must be encoded as an a). For example, OpenAI’s current tokenizer interface will remove homoglyphs. You must consider homoglyphs if you want to deploy an LLM on your hardware or a user’s device.

</details>

### 2.3 分词与LLM能力

如果我们只关心LLM生成高质量类人文本的能力，那么具体如何对文本进行分词，其重要性远不及用于构建这些模型的数据和计算资源。只要给模型投入足够的计算能力和规模，无论基础构建块如何，它们最终都能学会有用的表征。但有时，分词方式会显著影响LLM的能力。本节将列举一些例子。

接下来的例子可能与你用LLM的工作或目标并无直接关系。这完全没关系；这些例子的目的不是劝退你使用LLM。相反，目的是帮助你理解，LLM所学的内容范围受限于所选的表征方式，而如果不进行重大的工程改造，这些顾虑可能无法回避。如果你开始用LLM构建应用并遇到重大困难，请思考分词方式是否成为你目标实现中的一个因素。如果问题确实出在分词上，你能做的应对措施也很有限，因此最好考虑其他方法，比如手动扩充词汇表，加入对你应用重要的token。

<details>
<summary>英文原文</summary>

If we are only concerned with the ability of an LLM to produce high-quality human-like text, the specific details of how you tokenize your text do not matter as much as the data and compute used to build these models. If you put enough computational power and scale into your models, they will eventually figure out useful representations regardless of the building blocks. But sometimes, tokenization dramatically affects what an LLM is capable of. In this section, we cover some examples. It may be the case that the examples that follow are not directly relevant to your job or what you would like to do with an LLM. That is perfectly fine; the point of these examples is not to dissuade you from using an LLM. Instead, the goal is to help you understand that the scope of what LLMs learn is limited by the representation chosen, and there may not be a way around these concerns without major engineering work. If you start building an application with LLMs and find significant difficulty, think about how tokenization could be a factor in your goal. If tokenization is indeed the problem, there is little you can do to solve it, so it may be best to look at other approaches, such as manually augmenting the vocabulary with tokens that are important for your application.

</details>

### 2.3.1 LLMs不擅长文字游戏

大语言模型解字谜或执行涉及单词游戏的任务。

例如，图2.7展示了一个单词游戏，该游戏的正确答案取决于单词的精确字母序列和字母数量。

<details>
<summary>英文原文</summary>

LLMs to solve word puzzles or perform tasks that involve word games. For example, figure 2.7 shows a word game where the correct answer depends on the exact letter sequence and the number of letters in a word.

</details>

![图 2.7 分词方法意味着ChatGPT实际上无法“看到”单个字符或单词长度。如果你问需要识别子字符的问题并以独特不寻常的方式改变它们，ChatGPT就会开始失败。中间的正确字符是“a”，但ChatGPT坚持认为是“e”。ChatGPT看到的是三个to](assets/fig-2-7-7b754dd023.jpg)

*图2.7 分词方法意味着ChatGPT实际上无法“看到”单个字符或单词长度。如果你问需要识别子字符的问题并以独特不寻常的方式改变它们，ChatGPT就会开始失败。中间的正确字符是“a”，但ChatGPT坚持认为是“e”。ChatGPT看到的是三个token，分别代表P、ine和apple。*

玩文字游戏可能不是你的应用所关心的，但文字游戏失败的原因却可能和你的问题高度相关。尽管许多这样的例子都是玩具问题，在科学或商业上并不特别重要，但它们揭示了这些模型运行方式中值得注意的失效模式。这些问题可能会在更实际的应用中显现出来，例如当模型难以写出包含押韵或谐音的诗歌时。

考虑这样一个例子：你想构建一个回答用户关于处方药问题的应用。药物通常有较长、容易混淆的名称，人们往往记不住或拼写错误，而由于LLM不理解字母，它可能将一种药物的名称与另一种药物的又长又怪的名称混淆。

由于药物名称不常见，即使只有轻微的拼写错误，它们也会被分割成不同的词元。例如，在GPT-3中，“Amoxicillin”和常见的拼写错误“Amoxicillan”没有共同的词元！这大大增加了LLM回答错误的风险，而风险本身又很高，因此LLM应用更需要全面测试、极其小心地规避问题，或者可能完全避免使用。

<details>
<summary>英文原文</summary>

Playing word games may not be something you care about for your application, but the reason word games fail may be highly salient to your problem. Although many examples like this are toy problems in that they aren’t particularly scientifically or commercially important, they reveal notable breakdowns in how these models operate. They may come into play in more practical uses, such as when models struggle to write poetry containing rhymes or assonance. Consider, for example, that you want to build an application that answers ques-tions about a user’s prescription drugs. Drugs often have longer, confusing names that people fail to remember or spell incorrectly, and because an LLM does not understand letters, it may confuse one drug’s name with a different drug’s long and strange name.

Because drug names are uncommon, they will tokenize differently, even with minor misspellings. For example, in GPT-3, “Amoxicillin” and the easy misspelling “Amoxicillan” share no common tokens! This creates a much greater risk of the LLM responding incorrectly, where the risk is intrinsically higher, making an LLM application all the more important to thoroughly test, engineer around with extreme care, or potentially avoid altogether.

</details>

### 2.3.2 大语言模型在数学上面临挑战

分词显著影响涉及形式符号推理的任务，包括数学和下棋。数学和下棋在LLM中都被实现为符号推理问题，其中每个分词都有特定的规则来支配它们与其他分词结合时的交互和含义。例如，包含每个数字独立分词的模型在算术任务上通常比没有的模型表现更好。这是因为数字123456在GPT-3中会变成两个分词["123", "456"]，这是基于这些分词在分词器原始训练数据中的频率。这使得模型难以处理该数字中的单独数字。一些系统开发者通过在所有数字之间插入空格来归一化数字，例如1 2 3 4 5 6，从而创建一个包含六个分词的新输出，每个数字对应一个分词。

这种数学能力的差异在图2.8中得到了很好的展示，该图显示了整个训练过程中算术计算的性能。上方的曲线是典型的BPE分词器，而下方表现更好的曲线是同一个分词器经过修改，实现了数字的按位分词。

<details>
<summary>英文原文</summary>

Tokenization significantly affects tasks involving formal symbolic reasoning, including mathematics and playing board games. Both math and board games are implemented by LLMs as symbolic reasoning problems where individual tokens have specific rules governing their interactions and meaning when observed in conjunction with other tokens. For example, models containing individual tokens for each digit tend to perform better at arithmetic than models that don’t. This is because the number 123456 will become two tokens in GPT-3, ["123", "456"], based on the frequency of those tokens in the tokenizer’s original training data. This makes it harder for the model to deal with the individual digits in that number. Some system developers have solved this problem by normalizing numbers by inserting spaces between all digits, such as 1 2 3 4 5 6, which creates a new output with six tokens, one for each digit.

This difference in math capability is well-illustrated in figure 2.8, which shows performance on arithmetic computations throughout training. The top curve is a typical BPE tokenizer, while the bottom curve, which shows better performance, is the same tokenizer modified to have digit-level tokenization of numbers.

</details>

![图 2.8 展示了两个大语言模型随时间学习算术计算能力的对比。x轴表示时间。上曲线为典型的BPE分词器，下曲线为同一分词器但经过修改，使用表示单个数字的分词。y轴描述模型准确执行计算的能力，数值越小表示错误越少。底线是：采用按数字分词的大语言模型能够更](assets/fig-2-8-0a958765b5.jpg)

*图2.8 展示了两个大语言模型随时间学习算术计算能力的对比。x轴表示时间。上曲线为典型的BPE分词器，下曲线为同一分词器但经过修改，使用表示单个数字的分词。y轴描述模型准确执行计算的能力，数值越小表示错误越少。底线是：采用按数字分词的大语言模型能够更好、更快地学会数学。*

### 2.3.3 LLM与语言公平

大多数LLM分词器能够表示Unicode涵盖的所有符号，包括世界上大多数语言的字符。然而，这些分词器在表示不同语言文本时的效率差异极大，特别是由于分词器通常基于不同语言的小规模文本资源集进行训练。这可能导致基于LLM的商业服务出现显著的不公平性[5]，因为在训练集中罕见的语言，其单词的分词会默认分解为更细粒度的子词，从而导致token使用量增加。OpenAI和Anthropic等商业LLM提供商通常按token计费，每个输入LLM或由LLM生成的token通常收取不到一美分的费用。考虑到高使用量的商业应用每天可能处理数千万个token，这些成本会迅速累积。LLM完成请求所需的时间以及用户为每个token支付的费用直接取决于分词器。因此，分词器高效表示的语言在经济上比那些未被高效表示的语言更具优势。以英语为基准，研究人员发现，在使用ChatGPT和GPT-4时，回答德语或意大利语用户查询的成本约高出50%。与英语差异更大的语言产生的费用可能更高：图姆布卡语和保加利亚语的费用是英语的两倍以上，而宗卡语、奥里亚语、桑塔利语和掸语的处理成本是英语的12倍以上。

<details>
<summary>英文原文</summary>

Most LLM tokenizers can represent any symbol covered by Unicode, which includes the characters from most of the world’s alphabets. However, how efficiently those tokenizers represent text in a given language varies massively, especially as the tokenizers are typically trained on smaller collections of text resources for diffe-rent languages. This can cause substantial inequity in commercial services based on LLMs [5] because tokenization of words in languages that are rare in the training set defaults to a more granular set of subwords, resulting in increased token usage. Commercial LLM providers like OpenAI and Anthropic typically charge customers on a per-token basis, usually a fraction of a cent for every token input into the LLM and produced as output by the LLM. These costs add up when you consider that a high-use commercial application may process tens of millions of tokens daily. The time it takes for an LLM to complete a request and the amount a user is charged per token depends directly on the tokenizer. Therefore, languages that are more efficiently represented using a tokenizer are economically incentivized over those that are not represented efficiently. Using English as a baseline, researchers have found that the cost to answer a user query in German or Italian is about 50% more when using ChatGPT and GPT-4. Languages that differ even more substantially from English can incur much larger charges: Tumbuka and Bulgarian are more than twice the cost, and Dzongkha, Odia, Santali, and Shan cost over 12 times as much as English to process.

</details>

### 2.4 检查你的理解

1 你期望以下词语或短语如何被分词？尝试自己将它们拆分，然后通过一个实际的LLM分词器运行，例如 https://platform.openai.com/tokenizer 上的那个。

<details>
<summary>英文原文</summary>

1 How would you expect the following words or phrases to be tokenized? Try breaking them out yourself and then running them through an actual LLM tokenizer, such as the one at https://platform.openai.com/tokenizer:

</details>

backstopped large language models Schoolhouse 你如何处理句子以理解它们是一个复杂的过程，随着年龄增长而变化，涉及多个顺序和并行的认知过程。2 你认为对于之前的每个例子，大写字母与小写字母的影响有多大？

尝试用不同的大小写再次提交它们。

<details>
<summary>英文原文</summary>

backstopped large language models Schoolhouse How you process sentences to understand them is a complex process that changes as you get older and involves multiple sequential and concurrent cognitive processes 2 How much do you think uppercase versus lowercase letters matter for each of the previous examples? Try submitting them again with various casings.

</details>

既然 token 是大模型操作的基本单元，那么从技术角度来看，为什么 tokenizer 表示效率较低的语言会成本更高呢？

<details>
<summary>英文原文</summary>

4 Since a token is the basic unit an LLM operates on, why does it make sense (technologically) that languages less efficiently represented by a tokenizer would cost more?

</details>

大型语言模型根据用户使用的语言对同一服务收取不同费用，这存在伦理问题吗？

你是否认为这是一种歧视？

<details>
<summary>英文原文</summary>

5 Is it an ethical problem that LLMs charge different amounts to people for the same service based on what language they speak? Would you consider this discrimination?

</details>

### 2.5 语境中的分词

本章讨论的分词细节是大语言模型的基础构建块，它决定了模型能有效表示的输入以及生成的输出。分词是ChatGPT等大语言模型的关键组件，用于开发有效的文本表示，使得模型在训练过程中面对海量信息时能够学习词元之间的关系，从而理解用户输入并生成我们习以为常的高质量回复。大语言模型的潜力受限于或得益于所采用的分词策略和词汇表，以及我们在后续章节中探讨的其他所有特性。

<details>
<summary>英文原文</summary>

The details of tokenization we discuss in this chapter are the foundational building blocks of LLMs that govern the input they can represent effectively and the output they produce. Tokenization is a critical component of LLMs like ChatGPT in develop-ing effective representations of text so that they can be used to learn relationships between tokens when presented with vast amounts of information in the training process, interpreting user input and producing the high-quality responses we’ve become accustomed to. An LLM’s potential is limited or enabled by the tokenization strategy and vocabulary it employs, in conjunction with all of the other characteristics we explore in the following chapters.

</details>

### 总结

分词是LLM理解文本的基础过程，它将句子转换为词元。

词元是文本中表示内容的最小信息单元。有时它们对应完整的单词，但通常代表单词的一部分或子词。

分词涉及将文本标准化为统一表示，可能包括将字符转换为小写或转换Unicode字符的字节编码，使视觉上相同的字符使用相同编码。

分词还涉及分割，即将文本拆分为单词或子词。像字节对编码（BPE）这样的算法提供了一种机制，可以根据训练数据集中字母组合的统计出现来自动学习如何高效地分割文本。构建分词器的结果称为词汇表，它是分词器可以用来表示已处理文本的单词和子词词元的独特集合。

分词器词汇表的大小影响LLM准确表示数据的能力，以及理解和预测文本所需的存储和计算资源。

在LLM内部，词元用数字表示。因此，不存在对词元之间关系（如前缀和后缀，或两个词元共享相似字母集）的理解。为了支持特定知识领域，自动训练的分词器可以增强，以提供对其应用重要的词元。不理解单个字母或数字的分词器会在算术运算或简单文字游戏中出现问题。

<details>
<summary>英文原文</summary>

Tokenization is the fundamental process that LLMs use to understand text by converting sentences into tokens.

Tokens are the smallest units of information in text that represent content. Sometimes, they correspond to full words, but often, they represent pieces of words or sub-words.

Tokenization involves normalizing text into a standard representation, which may involve converting characters to lowercase or translating the byte encoding of Unicode characters so that visibly identical characters employ the same encoding.

Tokenization also involves segmentation, which is breaking up text into words or subwords. Algorithms like byte pair encoding (BPE) provide a mechanism to automatically learn how to efficiently segment text based on the statistical occurrence of combinations of letters in a training data set. The result of building a tokenizer is known as a vocabulary, which is the unique collection of word and subword tokens that a tokenizer can use to represent text it has processed.

The size of a tokenizer’s vocabulary affects the LLM’s ability to accurately repre-sent data and the storage and computational resources required to understand and predict text.

Internally to the LLM, tokens are represented using numbers. As a result, there is no understanding of relationships between tokens, such as prefixes and suffixes, or the fact that two tokens share a similar set of letters. To support specific domains of knowledge, tokenizers trained automatically may be augmented to provide tokens that are important to their application. Tokenizers that do not understand individual letters or digits will have problems with arithmetic operations or simple word games.

</details>



---

<a id="ch3"></a>

## 第 3 章: Transformer 架构：输入如何转换为输出

3 Transformer：输入如何变成输出

在第二章中，我们了解到大语言模型（LLM）将文本视为称为令牌的基本单元。现在，我们来探讨LLM如何处理这些令牌。LLM生成文本的过程与人类形成连贯句子的方式截然不同。当LLM运行时，它处理的是令牌，但同时它无法像人类那样操作令牌，因为LLM不理解每个令牌所代表字母的结构和关系。例如，英语使用者知道“magic”、“magical”和“magician”这些词是相互关联的。我们可以理解包含这些词的句子都指向同一主题，因为这些词共享同一词根。然而，LLM操作的是代表这些词的令牌的整数，如果不额外处理来建立这些联系，它就无法理解令牌之间的关系。

<details>
<summary>英文原文</summary>

In chapter 2, we saw how large language models (LLMs) see text as fundamental units known as tokens. Now it’s time to talk about what LLMs do with the tokens they see. The process that LLMs use to generate their text is markedly different from how humans form coherent sentences. When an LLM operates, it is working on tokens, yet simultaneously cannot manipulate tokens like humans do because the LLM does not understand the structure and relationship of the letters each token represents. For example, English speakers know that the words “magic,” “magical,” and “magician” are all related. We can understand that sentences containing these words are all connected to the same subject matter because these words share a common root. However, LLMs that operate on integers representing tokens that make up these words cannot understand the relationships between tokens without additional work to make those connections.

</details>

正因如此，LLM 延续了机器学习和深度学习领域中一种周期性转换的历史。首先，令牌（tokens）被转换为深度学习算法可处理的数值形式。然后，LLM 将这个数值表示再转换回一个新的令牌。这一循环会迭代重复，这与人类的工作方式截然不同。如果你的同事每说一个字都得掏出计算器做几道数学题，你肯定会大感担忧。

然而，这一过程恰恰是 LLM 产生输出的方式。本章将分两个阶段来阐述这一过程。首先，我们会从宏观角度回顾整个过程，介绍基本概念，并构建一个 LLM 生成文本的心理模型。接着，这一模型将作为框架，支持我们深入讨论 LLM 用于捕捉词与语言之间的关系、并最终生成我们所熟悉输出的组件细节和设计选择。

<details>
<summary>英文原文</summary>

For this reason, LLMs follow a long history in machine learning and deep learning of performing a kind of cyclical conversion. First, tokens are converted into a numeric form that deep learning algorithms can work on. Then, the LLM converts this numeric representation back into a new token. This cycle repeats iteratively, which is not comparable to how humans work. You would be incredibly concerned if your colleagues had to pull out a calculator to perform several math problems between each word they spoke.

Yet this process is, indeed, how LLMs produce outputs. In this chapter, we will walk through the process in two stages. First, we will review the entire process at a high level to introduce fundamental concepts and construct a mental model of how LLMs generate text. Next, this model will serve as a scaffolding for a more in-depth discussion of the details and design choices associated with the components that LLMs use to capture the relationships between words and language and, ultimately, generate the output we are familiar with.

</details>

### 3.1 Transformer模型

如今，你遇到的许多大型语言模型（LLM）都会使用一种称为Transformer的软件架构来解读令牌并生成输出。该架构包含一系列算法和数据结构，它们通过将信息表示为神经网络中的数字来进行存储。从本质上讲，Transformer是序列预测算法。虽然通常用“推理”或“理解”语言来描述它们，但它们实际所做的是预测令牌。Transformer提供了三种不同的令牌预测方法。虽然我们重点介绍的是著名的GPT架构（更正式的名称为仅解码器模型），但也有必要介绍一下仅编码器模型和编码器-解码器模型：

<details>
<summary>英文原文</summary>

Many LLMs you encounter today interpret tokens and produce output using a software architecture known as a transformer. This architecture consists of a collection of algorithms and data structures that store information by representing it as numbers in a neural network. At their core, transformers are sequence prediction algorithms. While it is common to describe them as “reasoning” or “understanding” language, what they actually do is predict tokens. Transformers come with three different approaches to token prediction. While we focus on the famous GPT architecture (more formally known as decoder-only models), it is also worth introducing encoder-only and encoder-decoder models:

</details>

- 仅编码器模型——这类模型旨在创建可用于执行任务的知识表示，即将输入编码为对算法更有用的数值表示。理解它们的最佳方式是将文本处理成机器学习算法更易使用的形式。它们广泛应用于科学研究。著名示例包括BERT和RoBERTa。

- 仅解码器模型——这类模型旨在生成文本。理解它们的最佳方式是接受一个部分写好的文档，然后通过预测下一个标记来生成该文档的可能续写。著名示例包括OpenAI的GPT和Google的Gemini。

- 编码器-解码器模型——这类模型也旨在生成文本。与仅解码器模型不同，它们接受整段文本并生成对应的段落，而不是续写已有内容。它们不如仅解码器模型流行，因为训练成本更高，而且使用起来有时更具挑战性。对于输入和输出序列明确的任务，编码器-解码器模型往往优于仅解码器模型。例如，它们在翻译和摘要任务上远优于仅解码器模型。著名示例包括T5和驱动谷歌翻译的算法。

<details>
<summary>英文原文</summary>

Encoder-only models—These models are designed to create knowledge represen-tations that can be used to perform tasks—that is, to encode the input into a numerical representation that is more useful to an algorithm. The best way to think of them is that they take text and process it into a form that is easier for a machine learning algorithm to use. They are widely used in scientific research.

Famous examples include BERT and RoBERTa.

Decoder-only models—These models are designed to generate text. The best way to think of them is that they take a partially written document and then produce a likely continuation of that document by predicting the next token. Famous examples include OpenAI’s GPT and Google’s Gemini.

Encoder-decoder models—These models are also designed to generate text. Unlike decoder-only models, they take an entire passage of text and create a correspon-ding passage rather than continue the existing one. They are less popular than decoder-only models because they are more expensive to train, and their use is sometimes more challenging. For tasks with a clearly defined input and output sequence, encoder-decoder models tend to outperform decoder-only models.

For example, they’re much better at translation and summarization tasks than decoder-only models. Famous examples include T5 and the algorithm that powers Google Translate.

</details>

无论使用哪种类型的Transformer，模型的基本组件都由三个基础层构成，只是内部排列方式不同。一个合理的类比是汽油内燃机：它们的工作原理相似，拥有相同的基本组件。这些组件（即层）在引擎（即Transformer）中的组合方式不同，会导致性能上不同的折中。

<details>
<summary>英文原文</summary>

Regardless of which type of transformer is used, the essential components of the model are built from three basic layers, just arranged in different ways internally. A reasonable analogy to their interchangeability is that of gasoline car engines: they all work similarly and have the same general components. How those components (read: layers) are put together within the engine (read: transformer) elicits various tradeoffs in performance.

</details>

LLM 是如今被称为神经网络的数百种算法之一。

然而，这个称谓在多个方面是误称。首先，如今构成神经网络方法的内容极其宽泛，以至于提及“基于神经网络的方法”并不能让读者对所述具体方法有太多了解。其次，名称中的“神经”部分与神经科学或大脑运作方式几乎毫无关系。有时确实存在一种直观的灵感：“嘿，大脑似乎也是这样做的；我们能模仿这种行为并从中获得有用的东西吗？”但对当前大多数方法而言并非如此。第三，神经网络更多描述的是一种组装数据结构的标准约定，而非特定算法。想想建房子：你使用二乘四木料、石膏板，以及橱柜、油漆和设计选择等多种选项，将一切组合成家。每栋房子看起来既独特又熟悉：它们都以预期的方式组装而成。神经网络的“层”是最小的组件，但你可以以不同方式使用多种类型的层。Transformer 是组装成更大网络的众多部件之一。

<details>
<summary>英文原文</summary>

LLMs are one of many hundreds of algorithms that we now call neural networks.

However, this is a misnomer in several ways. First, what constitutes a neural net-work approach today is very broad, to such a degree that referencing a “neural network–based approach” does not give the reader too much information about the exact approach described. Second, the neural part of the name has little or nothing to do with neuroscience or how the brain works. Sometimes, there is an intuitive “Hey, the brain kinda does something like this; can we mimic that be-havior and get something useful out of it?” style of inspiration, but not for most current methods. Third, a neural network describes more of a standard agreement on assembling data structures rather than a particular algorithm. Think about build-ing a house: you use two-by-fours, sheetrock, and many options for cabinetry, paints, and design choices to assemble everything into a home. Each home looks unique but also familiar: they are all assembled in an expected way. The “layer” of a neural network is the smallest component, but you can use many types of layers in diffe-rent ways. Transformers are one of many pieces that get assembled into a larger network.

</details>

### 3.1.1 Transformer 模型的层

图3.1描述了Transformer模型的核心组成部分：嵌入层，生成能够承载更多含义的Token表示；Transformer层，基于词语关系进行预测；以及输出层，将Transformer内部使用的数值表示转换为人类可读的文字。

<details>
<summary>英文原文</summary>

Figure 3.1 describes the essential components of the transformer model: the embedding layer, which generates representations of tokens that can hold more meaning; the transformer layer, which makes predictions based on word relationships; and the output layer, which transforms the numeric representations used within the transformer into words that humans can read.

</details>

![图 3.1 Transformer模型的基本组成部分，包括嵌入层、多个Transformer层以及输出层](assets/fig-3-1-69650cfccf.png)

*图3.1 Transformer模型的基本组成部分，包括嵌入层、多个Transformer层以及输出层*

让我们详细看看这些层：

<details>
<summary>英文原文</summary>

Let’s look at these layers in detail:

</details>

- 嵌入层——嵌入层将原始token作为输入，并将其映射为捕捉每个token含义的表示。例如，在第2章中，我们讨论了token如何表示概念，但各个token之间并不存在任何关系。考虑单词“dog”和“wolf”。根据我们对语言的理解，我们知道这些词是相关的，但我们需要某种方式在神经网络中捕捉这种关系。这正是嵌入层所做的工作。它捕捉每个token的信息，编码其含义，并允许我们表达它与其他token的概念关系。因此，我们可以捕捉到这样的概念：token dog和wolf的表示彼此之间比token red和France的表示更为相似。你可以将嵌入层视为模型的一部分，它处理页面上的单词，并将其映射到你脑海中的抽象概念表示。

- Transformer层——Transformer层是语言模型中大部分计算发生的地方：它们捕捉由嵌入层产生的单词之间的关系，并承担获取输出的大部分实际工作。虽然LLM通常只有一个嵌入层和一个输出层，但它们拥有许多Transformer层。更强大的模型拥有更多的Transformer层。人们很容易将Transformer层描述为模型的“思考”部分。这种定义错误地暗示Transformer层（或由它们构建的更大模型）能够思考，但人类的思考是自我反思的，并且在持续时间和努力程度上是可变的。你可以思考某件事半秒钟或几个月，取决于任务所需的努力。Transformer总是以相同的努力重复相同的过程来处理每一项任务。没有内省，也无法改变Transformer层的心理状态。因此，更好的方式是将Transformer层想象为一组模糊规则——模糊是因为它们不要求精确匹配（因为嵌入可能返回类似“dog”到“wolf”的相似物），规则是因为Transformer没有灵活性。一旦学习完成，Transformer层每次都会做同样的事情。

- 输出层——在模型完成计算后，输出层会执行额外的变换以获得有用的结果。最常见的是，输出层作为嵌入层的逆操作，将计算结果从捕捉概念的嵌入空间转换回捕捉实际子词的token空间，以构建文本输出。你可以将其视为模型的一部分，它接受你已经决定的答案，然后通过选择最有可能代表构成答案的概念的单词，来选择实际词语在页面上表达该答案。最后，我们以解嵌入过程结束，该过程将嵌入转换为token。由于每个token与子词具有一一对应关系，我们可以使用简单的字典或映射将token再次转换为人类可读的文本。该过程在图3.2中详细说明。

<details>
<summary>英文原文</summary>

Embedding layer—The embedding layer takes raw tokens as input and maps them into representations that capture each token’s meaning. For example, in chapter 2, we discussed how tokens represent concepts, but individual tokens don’t have any relationship with each other. Consider the words “dog” and “wolf.” With our understanding of language, we know these terms are related, but we need some way of capturing this relationship within a neural network. This is precisely what the embedding layer does. It captures information about each token that encodes its meaning and allows us to express its conceptual relationship with other tokens. Consequently, we can capture the idea that the representations of the tokens dog and wolf are more similar to each other than the representations for the tokens red and France. You can think of the embedding layer as the part of the model that processes the words on a page and maps them to abstract conceptual representations in your head. Transformer layer—Transformer layers are where most of the computation happens in a language model: they capture the relationships between words created by the embedding layer and do the bulk of the actual work to obtain the output. While LLMs generally only have one embedding layer and one output layer, they have many transformer layers. More powerful models have more transformer layers.

It is tempting to describe the transformer layer as the “thinking” part of the model. This definition erroneously implies that transformer layers (or the larger model built from them) can think, but thinking as humans do is self-reflecting and variable in duration and effort. You can think about something for a half-second or months, depending on the effort needed for the task. A transformer always repeats the same process with the same effort for every task. There is no introspection and no altering a transformer layer’s mental state. Thus, a better way to imagine a transformer layer is a set of fuzzy rules—fuzzy because they do not require exact matches (because embeddings might return something similar like “dog” to “wolf”) and rules because transformers have no flexibility. Once learning is complete, a transformer layer will do the same thing every time.

Output layer—After the model has done the computation, additional transforma-tions are performed in the output layer to obtain a useful result. Most commonly, the output layer operates as the inverse of the embedding layer, transforming the result of the computation from the embeddings space, which captures concepts, back into token space, which captures actual subwords to build text output. You can think of this as the part of the model that takes the answer you’ve decided on and then chooses the actual words to express that answer on a page by selecting the words most likely to represent the concepts that make up the answer. Finally, we end with an unembedding process, which converts the embeddings into tokens. Because each token has a one-to-one mapping to a subword, we can use a simple dictionary or map to convert the tokens into human-readable text again. This process is detailed in figure 3.2.

</details>

### 3.2 详细探索Transformer架构

为了进一步理解LLM内部发生的过程，重新审视我们之前描述的一系列步骤会很有帮助。因此，我们在图3.2中这样做，该图描述了七个步骤。我们将为每个步骤标记出之前讨论过的章节，或者告知你哪些是我们即将解释的新细节。本章一次性提供了大量信息，因此我们将逐步分解，逐一讲解。

<details>
<summary>英文原文</summary>

To further understand what is happening inside an LLM, it can be helpful to reframe what we described as a sequence of steps. So let us do that in figure 3.2, which describes seven steps. We’ll mark each of these with reference to the section where we covered it before or tell you when it is a new detail we are about to explain. This chapter provides a lot of information at once, so we will break it down piece by piece as we go.

</details>

![图 3.2 使用大型语言模型将输入转换为输出的过程](assets/fig-3-2-59c9ee608e.png)

*图3.2 使用大型语言模型将输入转换为输出的过程*

1. 将文本映射到令牌（见第2章）。

2. 将令牌映射到嵌入空间（新内容，见3.2.1节）。

3. 为每个嵌入添加信息，以捕获每个令牌在输入文本中的位置（新内容，见3.2.1节）。

4. 将数据通过一个Transformer层（重复L次）（新增，第3.2.2节）

5. 应用解嵌入层以获取可能生成良好响应的token（新增，第3.2.3节）

6. 从可能的token列表中采样以生成一个单一响应（新增，第3.2.3节）

7. 将响应中的令牌解码为实际文本（第2章）。

<details>
<summary>英文原文</summary>

1 Map text to tokens (chapter 2).

2 Map tokens into embedding space (new, subsection 3.2.1).

3 Add information to each embedding that captures each token’s position in the input text (new, subsection 3.2.1).

4 Pass the data through a transformer layer (repeat L times) (new, subsection 3.2.2).

5 Apply the unembedding layer to get tokens that could make good responses (new, subsection 3.2.3).

6 Sample from the list of possible tokens to generate a single response (new, subsection 3.2.3).

7 Decode tokens from the response into actual text (chapter 2).

</details>

![图 3.3 如果只用一个数字表示一个令牌，很快就会遇到相似/不相似的词无法相互适配的问题。这里我们看到，即使只有几个词，试图表示简单的同义/反义关系也会很快变得毫无意义。](assets/fig-3-3-3a03c9859c.png)

*图3.3 如果只用一个数字表示一个令牌，很快就会遇到相似/不相似的词无法相互适配的问题。这里我们看到，即使只有几个词，试图表示简单的同义/反义关系也会很快变得毫无意义。*

分词、嵌入以及语言如何精确转化为模型可理解的形式，这其中有许多细微之处。最重要的细微之处在于，神经网络仍然不直接处理词元。总体而言，神经网络需要可操作的数字，而每个词元都有固定的数字标识。我们不能改变词元的标识，因为该标识使我们能够将词元转换回人类可读的文本。我们需要一个层，将数字形式的词元转换为其所代表的单词或子词。

<details>
<summary>英文原文</summary>

There are a lot of nuances to tokenization, embeddings, and how precisely language gets translated into things that models can understand. The most important nuance is that neural networks still don’t work with tokens directly. On the whole, neural networks need numbers that can be manipulated, and a token has a fixed numeric identity. We cannot change the identity of a token because the identity allows us to convert tokens back to human-readable text. We need a layer that will transform tokens in numeric form into the words or subwords they represent.

</details>

例如，假设我们有一个代表“股票”的词元，我们随意决定将其转换为某个数字（比如5.2）。我们希望给相关的金融词汇（如“资本”）赋予相近的数字（比如5.3），因为它们含义相似。此外，“股票”还有其他含义的反义词，例如“稀有”。假设我们用负值来表示反义词的概念，并赋予其数值-5.2。但问题变得复杂了，因为“资本”的另一个反义词是“债务”。但如果反义词就是取负值，那么“债务”和“稀有”的含义就相似了，这显然荒谬。图3.3展示了这个问题：当我们用一个数字来表示一个词时，无法在不隐含与其他词产生奇怪关系的情况下编码词间关系，而这还仅仅考虑了四个词！

<details>
<summary>英文原文</summary>

For example, say we have a token for stock that we have arbitrarily decided will be converted to some number (e.g., 5.2). I want to give related financial words, such as capital, a similar number (e.g., 5.3) because they have similar meanings. There are also antonyms of stock’s other meanings, such as rare. Let’s say we use a negative value to capture the idea of an antonym and give it a value of -5.2. But now things get complex because another antonym of capital is debt. But if antonyms are negations, debt and rare have a similar meaning, which is nonsensical. Figure 3.3 illustrates the problem: when we use a single number to represent a word, we cannot encode their relationships without implying weird relationships with other words, and we have not even gotten past four words yet!

</details>

![图 3.4 为词元表示增加一个维度，使我们能够表示更多样化的语义关系排列。这里我们看到两个维度如何捕捉同一单词多种含义之间的关系。](assets/fig-3-4-6275a069ac.png)

*图3.4 为词元表示增加一个维度，使我们能够表示更多样化的语义关系排列。这里我们看到两个维度如何捕捉同一单词多种含义之间的关系。*

诀窍在于使用多个数字来表示每个token，从而能够找到更好的表示，以容纳词语之间的不同关系。图3.4展示了一个使用两个数字的例子。我们可以看到，比如平淡与三分熟和全熟几乎等距，同时银行远离前面提到的三个词，反而接近股票。我们甚至还能加入几个额外的词。使用的数字越多（在领域术语中称为维度），就能表示越复杂的关系。

<details>
<summary>英文原文</summary>

The trick is to use multiple numbers to represent each token, allowing you to find better representations that accommodate the different relationships between words. An example that uses two numbers is shown in figure 3.4. We can see things like bland being nearly equidistant from rare and well-done, while also having space for bank to be far away from all three just mentioned words and instead be near stock. We were even able to throw in a few extra words. The more numbers you use, called dimensions in the field’s jargon, the more complex relationships you can represent.

</details>

维数灾难。如果更多维度能更好地捕捉细微含义，为什么不尽可能多地使用维度来表示数据呢？处理大量维度时，会出现几个问题。一个主要问题是，LLM需要处理大量嵌入向量，而增加维度会提高存储和处理嵌入所需的内存和计算量。此外，随着维度增加，语义空间的规模暴增，训练机器学习模型以学习语义空间中所有位置所需的数据量和时间也呈指数级增长。数学家Richard E. Bellman创造了“维数灾难”这一术语来描述这一现象，因为尽管我们希望创建一个能够捕捉细微含义的空间，却受到所创建空间基本属性的限制。

<details>
<summary>英文原文</summary>

The curse of dimensionality If more dimensions are better at capturing subtle meaning, why not use as many dimensions as possible to represent our data? When dealing with a large num-ber of dimensions, several problems arise. One primary concern is that LLMs deal with many embeddings, and adding more dimensions increases the memory and computation required to store and process embeddings. Furthermore, as we add more dimensions, the size of the semantic space explodes, and the amount of data and time needed to train a machine learning model to learn about all locations in the semantic space similarly grows exponentially. Mathematician Richard E. Bell-man coined the term the “curse of dimensionality“ to describe this phenomenon because while we want to create a space capable of capturing nuanced meaning, we are limited by the fundamental properties of the space we create.

</details>

在LLM术语中，用于表示词元的数字列表称为嵌入（embeddings）。可以将嵌入视为一个浮点数值的数组或列表。为简便起见，我们称这种数组为向量（vectors）。向量中的每个位置称为维度（dimension）。如图3.4所示，使用多个维度可以捕捉人类语言中单词之间关系的细微差别。由于嵌入存在于多个维度中，我们常说它们位于语义空间（semantic space）中。在某些机器学习应用中，尤其是在不涉及文本时，这被称为潜在空间（latent space）。语义空间是一个模糊的术语，在该领域中没有明确定义，但最常用作一种简写，表示代表每个词元的向量嵌入具有良好的性质：同义词/反义词具有更近/更远的距离，并且我们可以高效利用这些关系。例如，在图3.5中，我们展示了一个著名的案例：通过减去男性嵌入并加上女性嵌入，可以构建一个“变为女性”的变换。这个变换可以应用于许多不同的男性性别词，以找到相同概念的女性性别词。图3.5右下角所有“皇室”相关词的共现也是有意为之，因为在高维空间中，可以同时维持多种不同类型的关系。

<details>
<summary>英文原文</summary>

In LLM parlance, the lists of numbers used to represent tokens are referred to as embeddings. You can think of an embedding as an array or list of floating-point values. As a shorthand, we call such arrays vectors. Each position in the vector is called a dimension. As we show in figure 3.4, using multiple dimensions allows us to capture subtleties in relationships between words in human language. Since embeddings exist in multiple dimensions, we often state that they live in a semantic space. In some machine learning applications, this is called a latent space, especially when not dealing with text. Semantic space is wishy-washy jargon that VJ 763 5190 isn’t well defined in the field, but it is most commonly used as a shorthand for saying that the vector embeddings that represent each token are well behaved in that synonyms/antonyms have nearer/farther distances and that we can use those relationships productively. As an example, in figure 3.5, we show a famous case where a “make female” transformation can be built by subtracting the embedding for male and adding the embedding for female. This transformation can be applied to many different male-gendered words to find female-gendered words of the same concept. The co-location of all the “royal” words in the bottom right of figure 3.5 is also intentional, as many different kinds of relationships can be simultaneously maintained in a high-dimensional space.

</details>

![图 3.5 展示了嵌入向量之间的关系如何构成语义空间。含义相近的词彼此靠近，且同一变换可应用于多个词以产生相似结果——本例中，变换用于寻找阳性词的阴性版本。](assets/fig-3-5-87fd891024.png)

*图3.5 展示了嵌入向量之间的关系如何构成语义空间。含义相近的词彼此靠近，且同一变换可应用于多个词以产生相似结果——本例中，变换用于寻找阳性词的阴性版本。*

令人惊讶的是，我们无法保证这些语义关系会在训练过程中形成。只是它们经常出现，并且被发现非常有用。以此类推，语义空间中的关系并非万无一失，数据中的偏差也可能渗入。例如，模型通常会判定"医生"更接近"男性"，而"护士"更接近"女性"，因为在构建大多数模型所使用的通用文本中，医生更常被描述为男性，护士更常被描述为女性。因此，这些关系并非发现的客观真理，而是对所输入数据的反映。

<details>
<summary>英文原文</summary>

Shockingly, we cannot guarantee that these semantic relationships will form during the training process. It just so happens that they often do, and they were discovered to be very useful. By extension, the relationships in a semantic space are not foolproof, and biases in your data can seep in. For example, models will often determine that doctor is more similar to male and nurse is more similar to female because, in the generally available text used to build most models, it is more common for doctors to be described as male and nurses as female. The relationships are thus not a discovered truth of the world but a reflection of the data that went into the process.

</details>

### 添加位置信息

标准Transformer的一个关键问题是它不理解序列信息。如果你给Transformer一个句子，并将所有token重新排列，它会将所有可能的token排列视为相同的！图3.6展示了这个问题。

<details>
<summary>英文原文</summary>

One critical problem is that a standard transformer does not understand sequential information. If you gave the transformer one sentence and rearranged all the tokens, it would view all possible permutations of the tokens as identical! That problem is illustrated in figure 3.6.

</details>

![图 3.6 如果没有位置信息，Transformer 就无法理解输入具有特定顺序，算法会将令牌的所有可能重排视为相同。这会产生问题，因为单词顺序会改变单词的上下文，如果随机排列，则会变成乱码。](assets/fig-3-6-fedde944ce.png)

*图3.6 如果没有位置信息，Transformer 就无法理解输入具有特定顺序，算法会将令牌的所有可能重排视为相同。这会产生问题，因为单词顺序会改变单词的上下文，如果随机排列，则会变成乱码。*

因此，嵌入层会生成两种不同的嵌入。首先，它创建捕获词元含义的词嵌入；其次，它创建捕获词元在序列中位置的位置嵌入。这个想法出奇地简单。正如我们将每个独特的词元映射到一个唯一的意义向量，我们也将把每个独特的词元位置（第一、第二、第三，依此类推）映射到一个位置向量。因此，每个词元将被嵌入两次——一次为其身份，一次为其位置。然后将这两个向量相加，得到代表该词及其在句子中位置的一个向量。图 3.7 概述了这一过程。

<details>
<summary>英文原文</summary>

For this reason, the embedding layer generates two different kinds of embeddings. First, it creates a word embedding that captures the meaning of the token, and second, it makes a positional embedding that captures the token’s location in a sequence. The idea is surprisingly simple. Just as we mapped every unique token to a unique meaning vector, we will also map every unique token position (first, second, third, and so on) to a position vector. So each token will get embedded twice—once for its identity and again for its position. These two vectors are then added to create one vector representing the word and its location in the sentence. This process is outlined in figure 3.7.

</details>

![图 3.7 词嵌入无法捕捉输入token出现的顺序信息，这一信息由位置嵌入捕获。位置嵌入的工作原理与词嵌入相同，两者相加后得到新的组合嵌入，其中包含了模型理解token顺序所需的信息。](assets/fig-3-7-4570bb0976.png)

*图3.7 词嵌入无法捕捉输入token出现的顺序信息，这一信息由位置嵌入捕获。位置嵌入的工作原理与词嵌入相同，两者相加后得到新的组合嵌入，其中包含了模型理解token顺序所需的信息。*

这些正是理解标记如何被转换成向量供 Transformer 层处理所需的全部细节。这种做法可能显得有点原始，事实也确实如此。人们曾尝试开发更复杂的方法来处理这些信息，但这种“把一切都向量化然后相加”的简单方法却出奇地有效。重要的是，它在视频和图像领域同样取得了成功。拥有一个足够应对多种问题的直接策略很有价值，这正是这种朴素方法得以流行起来的原因。

<details>
<summary>英文原文</summary>

Those are all the missing details required to understand how tokens are converted into vectors for the transformer layers. This strategy may seem somewhat naive, and that is honestly true. People have tried developing more sophisticated methods to handle this information, but this simple approach of “Let’s make everything a vector and just add them together” works surprisingly well. Importantly, it has also demonstrated success in video and images. Having a straightforward strategy that functions well enough for many different problems is valuable, which is why this naive approach has taken hold.

</details>

### 3.2.2 Transformer层

Transformer层旨在将输入转化为更有用的输出。大多数先前的神经网络层（如嵌入层）都设计为将关于世界如何运作的非常具体的信念融入其操作中。

其想法是，如果编码的信念与现实世界运作方式一致，模型就能用更少的数据得到更好的解决方案。

Transformer则采取了相反的策略。

它们编码了一种通用机制，只要有足够的数据，就能学习许多任务。

为此，Transformer由三个主要组件构成：

<details>
<summary>英文原文</summary>

The transformer layer aims to transform the input into a more useful output. Most prior neural network layers, such as an embedding layer, are designed to incorporate very specific beliefs about how the world works into their operation. The idea is that if the encoded belief is accurate to how the world does indeed work, your model will reach a better solution using less data. Transformers go for the opposite strategy. They encode a general-purpose mechanism that can learn many tasks if you get enough data. To do this, transformers operate with three primary components:

</details>

- 查询——查询是从嵌入层得到的向量，表示你要寻找的内容。

- 键——键向量表示与查询进行配对的可能答案。

- 值——每个键都有一个对应的值向量，即当查询与键匹配时返回的实际值。

<details>
<summary>英文原文</summary>

Query—Queries are vectors (from an embedding layer) that represent what you are looking for.

Key—Key vectors represent the possible answers to pair a query against. Value—Every key has a corresponding value vector, the actual value to be returned when a query and key match.

</details>

这一术语对应于Python中字典（dict）对象的行为。通过键来查找字典中的项，以便生成有用的输出。不同之处在于Transformer是模糊的。我们并不是在查找单个键，而是评估所有键，并根据它们与查询的相似度进行加权。图3.8通过一个简单示例展示了这一过程。虽然查询和键以字符串形式显示，但这些字符串代表的是每个字符串通过嵌入层映射到的向量。

如果每个键都为一个查询贡献力量，可能会造成混乱，尤其是在查询与某个特定键存在精确匹配的情况下。这个问题通过一个称为注意力或注意力机制的细节来解决。

Transformer中的注意力机制可以类比为你专注于重要事物的能力。你可以忽略无关和分散注意力的信息（即坏键），主要关注重要内容（最佳匹配键）。这个类比还可以进一步延伸：注意力是自适应的，重要程度取决于其他可选选项。老板给你布置本周任务会占据你的注意力，但火警响起时，你的注意力会从老板转移到警报（和可能发生的火灾）上。

在生成下一个token时，Transformer会获取当前token的查询，并将其与之前所有token的键进行比较。比较查询和键会生成一系列数值，注意力机制利用这些数值来计算

<details>
<summary>英文原文</summary>

This terminology corresponds to the behavior of a dict or dictionary object in Python. You look up an item in the dictionary by its key so that you can then create some useful output. The difference is that a transformer is fuzzy. It’s not that we are looking up a single key, but we are evaluating all keys, weighted by their degree of similarity to the query. Figure 3.8 shows how this works with a simple example. While the queries and keys are shown as strings, those strings are stand-ins for the vectors that each string will be mapped to via the embedding layer.

Having every key contribute to one query could be chaotic, especially if there is one true match between a query and a specific key. This problem is handled by a detail called attention or the attention mechanism.

Attention inside a transformer can be considered similar to your ability to pay attention to what is important. You can tune out irrelevant and distracting information (i.e., bad keys) and focus primarily on what is important (the best matching keys). The analogy extends further in that attention is adaptive; what is important is a function of what other options are available. Your boss giving you directions for the week takes up your attention, but the fire alarm going off changes your attention away from your boss to the alarm (and a potential fire).

When generating the next token, a transformer takes the query for the current token and compares it to the key for all previous tokens. Comparing the query and the key generates a series of values that the attention mechanism uses to calculate

</details>

![图 3.8 展示了Transformer内部查询、键和值的工作原理，并与Python字典进行对比。Python字典在将查询与键匹配时，需要精确匹配才能找到值，否则返回空。而Transformer总是基于查询与键之间最相似的匹配返回一些内容。](assets/fig-3-8-c5d83cd5dd.png)

*图3.8 展示了Transformer内部查询、键和值的工作原理，并与Python字典进行对比。Python字典在将查询与键匹配时，需要精确匹配才能找到值，否则返回空。而Transformer总是基于查询与键之间最相似的匹配返回一些内容。*

决定生成下一个词元时，应为每个可能的后续词元分配多少权重。每个词元的值告诉模型，前面的每个词元认为其对概率的贡献应该是多少。然后注意力函数计算下一个词元，如图3.9所示。

<details>
<summary>英文原文</summary>

how much weight it should assign each potential following token when deciding which token to generate next. The value for each token tells the model what each previous token thinks its contribution to the probability should be. The attention function then computes the next token, as shown in figure 3.9.

</details>

![图 3.9 句子中的下一个词元通过将当前词元作为查询，并计算与前面词（作为键）的匹配来预测。各个值本身不需要存在于语义空间中；注意力机制的输出产生与词汇表中某个词元类似的内容。](assets/fig-3-9-cf63e5522c.png)

*图3.9 句子中的下一个词元通过将当前词元作为查询，并计算与前面词（作为键）的匹配来预测。各个值本身不需要存在于语义空间中；注意力机制的输出产生与词汇表中某个词元类似的内容。*

注意力机制的数学原理是什么？

<details>
<summary>英文原文</summary>

What is the math of attention?

</details>

我们不会深入探讨注意力背后的每一个数学细节，因为要描述它需要大量篇幅，且在其他地方已有介绍。我们曾在先前的一本书中做过：〈深度学习内部〉[2] 的第11章以更多技术细节解释了Transformer和注意力。

<details>
<summary>英文原文</summary>

We will not go into every detail of the math behind attention because it would take a lot of space to describe it, and it has been covered elsewhere. We did so in a previous book: chapter 11 of Inside Deep Learning [2] explains transformers and attention in much greater technical detail.

</details>

对于好奇的读者，主要公式是 Attention = Softmax( (Q·K)/√d ) V (3.1)

<details>
<summary>英文原文</summary>

For the curious, the primary equation is Attention = Softmax Q · K √  V (3.1) d

</details>

查询、键和值分别由独立的矩阵 Q、K 和 V 表示。矩阵乘法使得注意力机制在 GPU 上高效实现，因为 GPU 可以并行执行大量乘法运算。softmax 函数通过将许多值赋为接近于零来实现注意力类比的主要组件，这使 transformer 忽略不重要的项。

<details>
<summary>英文原文</summary>

The queries, keys, and values are represented by individual matrices Q, K, and V , respectively. Matrix multiplication makes attention efficient when implemented on GPUs because they can perform many multiplication operations in parallel. The softmax function implements the main component of the attention analogy by assigning many values nearly equal to zero, which causes the transformer to ignore the unimportant items.

</details>

归一化与前馈网络的最后一步，是通过跳跃连接来应用层归一化和线性层。即便你对这些术语不熟悉，也没关系；读懂本书后续内容并不需要理解这些数学知识。如果你想了解这些术语的含义，我们推荐你参考《Inside Deep Learning》[2]，该书提供了技术细节的深入讲解。

<details>
<summary>英文原文</summary>

The final step of norm and Feedforward is the application of layer normalization and a linear layer via a skip connection. If these terms aren’t familiar to you, that is fine; you do not need to know this math to understand the rest of the book. If you want to learn what these terms mean, we refer you to Inside Deep Learning [2] for a technically detailed understanding.

</details>

一个Transformer模型由数十个Transformer层组成。中间Transformer层执行与图3.9所描述相同的机械任务，尽管它们不需要预测一个token，因为只有最后一个Transformer层需要预测实际的token。Transformer层足够通用，以至于组合许多中间层可以使模型学习复杂任务，如排序、堆叠和其他复杂的输入变换。

<details>
<summary>英文原文</summary>

A transformer model is made up of dozens of transformer layers. The intermediate transformer layers perform the same mechanical task described in figure 3.9 despite not having to predict a token because the last transformer layer is the only one that needs to predict an actual token. The transformer layer is general enough that combining many intermediate layers allows the model to learn complex tasks such as sorting, stacking, and other sophisticated input transformations.

</details>

### 3.2.3 解嵌入层

LLM的最后阶段是解嵌入层，它将transformer使用的数值向量表示转换为特定的输出token，以便我们最终能够返回与该token对应的文本。

这一输出生成过程也称为解码，因为我们解码transformer的向量表示得到一段输出文本。

它是使用LLM生成文本的关键组件。

解码当前token不仅对产生输出至关重要，而且下一个token将依赖于之前每个选中的输出token。

图3.10展示了这个过程，我们递归地一次生成一个token。

用统计学的术语来说，这被称为自回归过程，意味着输出的每个元素都基于其之前的输出。

<details>
<summary>英文原文</summary>

The last stage of an LLM is the unembedding layer, which transforms the numeric vector representation that transformers use into a specific output token so that we can ultimately return the text that corresponds to that token. This output generation process is also called decoding because we decode the transformer vector representa-tion to a piece of output text. It is a crucial component for using an LLM to generate text. Not only is decoding the current token essential for producing output, but the next token will depend on each previous token selected for output. This process is shown in figure 3.10, where we recursively generate tokens one at a time. In statistical parlance, this is known as an autoregressive process, meaning each element of the output is based on the output that came before it.

</details>

![图 3.10 LLM的输出生成包含从文档到token的转换，再通过模型产出输出。这一循环过程既用于处理输入文本，也用于生成可读的输出。](assets/fig-3-10-ce05095ba0.png)

*图3.10 LLM的输出生成包含从文档到token的转换，再通过模型产出输出。这一循环过程既用于处理输入文本，也用于生成可读的输出。*

你可能好奇这个过程是如何停止的。在构建词元词汇表时，我们会包含一些文本中不会出现的特殊词元。其中一种特殊词元是序列结束（EoS）词元。模型在带有自然结束点的文本上进行训练，这些文本以EoS标记结束；当模型生成一个新词元时，EoS词元是可生成选项之一。如果生成了EoS，我们就知道应该停止循环并将完整文本返回给用户。此外，如果模型陷入不良状态而未能生成EoS词元，设置一个最大生成限制也是一个好主意。

<details>
<summary>英文原文</summary>

You may be wondering how this process stops. When we build the vocabulary of tokens, we include some special tokens that do not occur in the text. One of these special tokens is an end of sequence (EoS) token. The model trains on texts with natural endpoints that are finished with the EoS marker, and when the model generates a new token, the EoS token is one of the options it can generate. If the EoS is generated, we know it is time to stop the loop and return the full text to the user. It is also a good idea to keep a maximum generation limit if your model gets into a bad state and fails to generate the EoS token.

</details>

### 采样 Token 生成输出

这个过程中缺失的是如何将transformer层生成的一个向量（一组浮点数数组）转化为一个词元。这个过程称为采样，因为它采用统计方法，根据LLM的输入和当前已生成的输出来从词汇表中选择候选词元。LLM的采样算法对这些候选进行评估，从而决定生成哪个词元。有几种不同的采样技术，但都遵循相同的基本两步策略：

<details>
<summary>英文原文</summary>

What is missing from this process is how we convert a vector, an array of floating-point numbers produced by the transformer layers, into a single token. This process is called sampling because it uses a statistical method to choose sample tokens from the vocabulary based on the LLM’s input and its output so far. The LLM’s sampling algorithm evaluates those samples to select which token to produce. There are several techniques for doing this sampling, but all follow the same basic two-step strategy:

</details>

1. 对词汇表中的每个词元，计算其成为下一个被选中词元的概率。

<details>
<summary>英文原文</summary>

1 For each token in the vocabulary, compute the probability that each token will be the next selected token.

</details>

2. 根据计算出的概率随机选取一个词元。

<details>
<summary>英文原文</summary>

2 Randomly pick a token according to the probabilities calculated.

</details>

如果你使用过ChatGPT或其他LLM，可能会注意到同样的输入并不总是产生同样的输出。解码步骤就是你每次问同一个问题却得到不同答案的原因。

随机选择token看似有悖直觉，但这是生成高质量文本的关键。以图3.11中的文本生成示例为例，我们试图补全句子“I love to eat.”。如果模型总是选择概率最高的“sushi”作为下一个token，那将是不切实际的。如果有人在这种情况下总对你说“sushi”，你会觉得不对劲。我们需要随机性来处理存在多个有效选择且并非所有选项都可能出现的事实。

<details>
<summary>英文原文</summary>

If you have used ChatGPT or other LLMs, you may have noticed that they do not always provide the same output for the same input. The decoding step is why you may get different answers whenever you ask the same question. It may seem counterintuitive that tokens are selected randomly. However, it is a critical component to generating good-quality text. Consider the example of text generation in figure 3.11, where we are trying to finish the sentence “I love to eat.” It would be unrealistic if the model always picked “sushi” as the next token because it had the highest probability. If someone always said “sushi” to you in this context, you would think something was off. We need randomness to handle the fact that there are multiple valid choices, and not all options are likely to occur.

</details>

2. 每个可能的词元都会计算一个概率，多数词元的概率接近于零。

<details>
<summary>英文原文</summary>

2. A probability is computed for each possible token, most receive near-zero probabilities.

</details>

BBQ 23%，bbq 7%

<details>
<summary>英文原文</summary>

BBQ 23% bbq 7%

</details>

3. 掷一次“加权骰子”来决定下一个词元。

<details>
<summary>英文原文</summary>

3. A weighted dice is “rolled” to decide which token is next.

</details>

![图 3.11 图 3.11** 我们以短语“I love to eat”开头演示文本生成，展示一些可能的补全——如食物 barbeque 和 sushi ——具有高概率，而汽车和数字 42 的概率很低。加权随机选择选出了单词 tacos。当出现 EoS ](assets/fig-3-11-51738c02c5.png)

*图3.11 **图 3.11** 我们以短语“I love to eat”开头演示文本生成，展示一些可能的补全——如食物 barbeque 和 sushi ——具有高概率，而汽车和数字 42 的概率很低。加权随机选择选出了单词 tacos。当出现 EoS 令牌时，生成循环停止。*

还要注意，在图3.11的例子中，其他token（如42）被赋予极小的概率，因此是毫无意义的。同样，我们需要为每个token分配概率，以判断哪些token可能出现或不可能出现。

<details>
<summary>英文原文</summary>

Also note in the example from figure 3.11 that other tokens would be nonsensical, like 42, given tiny probabilities. Again, we need to assign every token a probability to know which tokens are likely or unlikely.

</details>

如何获得token的概率？

<details>
<summary>英文原文</summary>

How do you get probabilities for tokens?

</details>

每个可能的下一词元被选中的概率各不相同。大多数词元被选中的概率几乎为零。细心的读者可能会问：在不知道其他词元的情况下，我们如何能为一个词元分配概率呢？我们的方法是给每个词元一个分数，表示该词元的嵌入与当前向量（即Transformer的输出）的匹配程度。这个分数是任意值，范围从负无穷到正无穷，并且每个词元独立计算。然后利用分数的相对差异来生成概率。例如，如果一个词元的分数是65.2，另一个是-5.0，那么这两个词元被选中的概率分别接近100%和0%。如果这两个分数分别是65.2和65.1，那么概率分别接近50.5%和49.5%。

<details>
<summary>英文原文</summary>

Each possible next token has a different probability of being selected. Most of the tokens have nearly zero chance of being selected. A keen reader may wonder: How can we assign a probability to a token before knowing the other tokens? We do so by giving every token a score, indicating how good a match that token’s embedding is compared to the current vector (i.e., the output from the transformer). The score is arbitrary from −∞to ∞and calculated independently for each token. The relative difference in scores is then used to create probabilities. For example, if one token had a score of 65.2 and a second token had a score of -5.0, the probabilities would be near 100% and 0% for the individual token, respectively. If the scores were 65.2 and 65.1, the probabilities would be near 50.5% and 49.5%, respectively.

</details>

类似地，分数0.2和0.1与分数65.2和65.1会给出相同的概率，因为我们是根据分数的相对差异来分配概率的，而不是分数本身。

<details>
<summary>英文原文</summary>

Similarly, scores of 0.2 and 0.1 would give the same probabilities as the scores 65.2 and 65.1 because we are looking at relative differences in scores to assign probabilities, not the individual scores themselves.

</details>

Transformer 有时会产生异常或无意义的生成内容。这种情况虽然不常见，但其他 token 的概率几乎为零，最终某个怪异的 token 会被选中，出乎你的意料。一旦选中了意外的 token，后续生成的所有 token 都会尝试让这个异常变得合理。

例如，如果 LLM 生成了“我爱吃粉笔”，你会相当惊讶。但这并非完全不合理，因为吃粉笔是一种名为异食癖的医学症状。一旦选定了“粉笔”这个词，LLM 可能会转向关于异食癖或其他医学话题的讨论——当然，前提是你足够幸运，你的异常生成属于“罕见但合理”的范畴，而不是完全错误的预测。

<details>
<summary>英文原文</summary>

A transformer sometimes gives you unusual or nonsensical generations. It’s not common, but the other tokens have a near-zero probability, and eventually, one weird token will get picked that you would not expect. Once an unexpected token has been chosen, all future generated tokens will be produced in a manner that tries to make sense of the unusual generation.

For example, if the LLM produced “I love to eat chalk,” you would be pretty surprised. But it is not overly unreasonable because chalk-eating is a symptom of the medical condition called pica. Once the word chalk is selected, the LLM may go into a tangent about pica or some other medical diatribe—that is, of course, if you are so lucky that your unusual generation is in the sphere of “rare but reasonable” and not an utterly errant prediction.

</details>

注意：许多算法可以计算用于选择生成词汇的最终概率。

其中一种是核采样（Top-p采样），它确定概率最高的令牌作为候选输出，并从该列表中选取输出的令牌。这种方法有助于避免不合理的预测。如果可能，你应该检查你的LLM使用了哪种采样算法，以便了解其产生从罕见到不合理输出的风险。

<details>
<summary>英文原文</summary>

NOTE Many algorithms can compute the final probabilities used to select words for generation. One of these is nucleus sampling, also known as Top-p sampling, which involves determining the tokens with the highest probability as potential outputs and choosing tokens to output from that list. This method can help us avoid unreasonable predictions. If you can, you want to check which sampling algorithm your LLM uses so that you can understand its risks of producing rarer to unreasonable outputs.

</details>

### 3.3 创造力与主题回应之间的权衡

根据用户计划与LLM交互的方式，可能希望生成令人惊喜或富有创意的输出。例如，你使用LLM来帮助头脑风暴新产品创意，并将聊天机器人作为激发灵感的数字回音板。在这种情况下，你可能希望产生不寻常的输出，因为目标是发挥创造力，想出新颖的东西。

相反，有时创意完全是不需要的。LLM的一个潜在用途是离线搜索，你可以将LLM安装在（相对强大的）手机上，即使在没有网络连接时也能查询信息。这种情况下，你希望LLM的输出可靠、切题且基于事实。不需要创造性的重新诠释。

LLM中一个名为“温度”的功能平衡了这一取舍。温度变量（一个介于0和1之间的数字，默认值通常为0.7或0.8）用于放大低概率令牌的概率（高温）或抑制低概率令牌的概率（低温）。

以一杯水中的分子作为类比。假设我们想知道哪个分子会出现在杯子顶部（别问为什么，先顺着这个思路）。如果将杯子降到绝对零度，所有分子将静止，杯子顶部的分子每次都会可靠地相同（即，你总是生成相同的令牌）。如果将杯子的温度升高到开始沸腾，分子会四处弹跳，使得杯子顶部的分子基本上是随机的（即，你得到完全随机的令牌）。当你上下调节温度时，你改变了在更大随机性（因此通常更具创造性）和只关注最可能的下一令牌（从而保持生成内容更切题）之间的平衡。

在实际应用中，以“我喜欢吃”为例，较高的温度会导致生成不同类型的食物，不仅是比萨或寿司，还可能是更不典型或更具体的食物，如威灵顿牛排或素辣酱。

<details>
<summary>英文原文</summary>

Depending on how your users plan to interact with an LLM, generating surprising or creative outputs may be desired. Say you are using an LLM to help brainstorm new product ideas, and you are using a chatbot as a digital sounding board to spark ideas. In this case, you probably want unusual outputs generated because the goal is to be creative and think of something new.

Conversely, sometimes creativity is wholly undesired. One potential use for LLMs is offline search, where you could fit an LLM on a (relatively powerful) mobile phone and ask/look up information even when you do not have internet connectivity. In this case, you want the outputs of the LLM to be reliable, on topic, and factual. A creative reinterpretation is not needed.

A feature in LLMs called temperature balances this tradeoff. The temperature variable (which is a number between 0 and 1 and often has a default value of 0.7 or 0.8) is used to exaggerate the probability of low-likelihood tokens (high temperature) or depress the probability of low-likelihood tokens (low temperature). Consider molecules in a glass of water as an analogy. Say we want to know what molecule will be at the top of the glass (don’t ask us why; just go with it). If the glass was lowered to a temperature of absolute zero, all the molecules would be still, and the molecule at the top of the glass would reliably be the same each time (i.e., you will always generate the same token). If you raise the temperature of the glass so much that it starts to boil, the molecules will bounce around, making the molecule at the top of the glass essentially random (i.e., you get a completely random token). As you scale the temperature up and down, you change the balance between picking with greater randomness (and, thus, often creativity) or focusing on just the most likely next token (thus keeping the generation more topical). In a practical sense, considering our example of “I like to eat,” a higher tempera-ture would lead to the generation of different types of foods, not just pizza or sushi but possibly less typical or more specific foods like beef wellington or vegetarian chili.

</details>

### 3.4 上下文中的Transformer

本章我们涵盖了很多内容。嵌入层、Transformer层和反嵌入层是让LLM工作的核心构建块。LLM如何编码含义和位置，然后使用堆叠的Transformer层来揭示文本中的结构，这些概念对于理解LLM如何捕获信息并产生其能力的输出质量至关重要。但我们还有更多细节要讲！我们如何通过分析大量数据来创建这些层进而生成嵌入和概率？在第四章中，我们将继续探索如何将数据输入这个架构，并通过训练过程激励LLM“学习”文本中有意义的关系。

<details>
<summary>英文原文</summary>

We’ve covered a lot of ground in this chapter. Embedding layers, transformer layers, and unembedding layers are the core building blocks that make LLMs work. The concepts of how LLMs encode meaning and position and then use stacks of transfor-mer layers to uncover the structure in text are all vital to understanding how LLMs capture information and produce the quality of output they are capable of. But we have more details to cover! How do we create these layers to generate embeddings and probabilities by analyzing piles and piles of data in the first place? In chapter 4, we will continue exploring how to feed data into this architecture and incentivize the LLM to “learn” meaningful relationships in text through the training process.

</details>

### 总结

虽然 LLM 以词元为基本语义单元，但在模型内部，它们并非以字符串形式，而是以嵌入向量的方式进行数学表示。这些嵌入向量能够捕获临近性、相异性、反义词等语言描述属性之间的关系。位置和词序并非变换器天然具备的特性，而是通过另一个表示相对位置的向量获得。模型通过将位置向量与词嵌入向量相加来表示词序。变换器层类似于一种模糊词典，对近似匹配返回近似答案。这一模糊过程称为注意力机制，它使用查询、键和值这三个术语，类似于 Python 字典中的键和值。

ChatGPT 是仅解码器变换器的一个例子，但也存在仅编码器变换器和编码器-解码器变换器。仅解码器变换器最擅长生成文本，但其他类型的变换器在其他任务上可能表现更佳。

LLM 是自回归的，这意味着它们递归工作。在每一步，所有先前生成的词元都被输入模型以获取下一个词元。简单来说，自回归模型利用之前的内容预测下一个内容。

<details>
<summary>英文原文</summary>

While LLMs use tokens as their basic unit of semantic meaning, they’re mathematically represented within the model as embedding vectors rather than as strings. These embedding vectors can capture relationships about nearness, dissimilarity, antonyms, and other linguistic-descriptive properties. Position and word order do not come naturally to transformers and are obtained via another vector representing the relative position. The model can represent word order by adding the position and word embedding vectors. Transformer layers act as a kind of fuzzy dictionary, returning approximate answers to approximate matches. This fuzzy process is called attention and uses the terms query, key, and value as analogous to the key and value in a Python dictionary.

ChatGPT is an example of a decoder-only transformer, but encoder-only transfor-mers and encoder-decoder transformers also exist. Decoder-only transformers are best at generating text, but other types of transformers can be better at other tasks.

LLMs are autoregressive, meaning they work recursively. All previously genera-ted tokens are fed into the model at each step to get the next token. Simply put, autoregressive models predict the next thing using the previous things.

</details>

任何Transformer的输出都不是词元，而是每个词元出现概率的概率分布。选择特定词元的过程称为“解嵌入”或“采样”，其中包含一定的随机性。

随机性的强弱可以控制，从而产生更逼真、更具创意或更一致的输出。大多数大语言模型都有一个默认的随机性阈值，看起来合理，但你可能需要根据不同的用途调整它。

<details>
<summary>英文原文</summary>

The output of any transformer isn’t tokens; instead, the output is a probability for how likely every token is. Selecting a specific token is called unembedding or sampling and includes some randomness.

The strength of randomness can be controlled, resulting in more or less realistic output, more creative or unique output, or more consistent output. Most LLMs have a default threshold for randomness that is reasonable looking, but you may want to change it for different uses.

</details>



---

<a id="ch4"></a>

## 第 4 章: LLM 如何学习

### 大语言模型如何学习

“学习”和“训练”这两个词在机器学习社区中常用来描述算法在观察数据并基于这些观察做出预测时的行为。我们不太情愿地使用这些术语，因为虽然它们简化了关于算法操作的讨论，但我们认为这并不理想。从根本上讲，这种术语会导致对 LLM 和人工智能的误解。这些词暗示算法具有类似人类的特质；它们诱使你相信算法展现出涌现行为，能力远超其实际所能。在根本层面上，这种术语是不正确的。计算机的学习方式与人类的学习方式没有丝毫相似之处。模型确实会根据数据和反馈进行改进，但极其重要的是，要在机制上将这与人类学习严格区分开来。事实上，你可能并不希望 AI 像人类那样学习：我们花费多年专注于教育，却仍然会做出愚蠢的决定。

<details>
<summary>英文原文</summary>

The words learning and training are commonly used in the machine learning com-munity to describe what algorithms do when they observe data and make predic-tions based on those observations. We use this terminology begrudgingly because although it simplifies the discussion of the operations of these algorithms, we feel that it is not ideal. Fundamentally, this terminology leads to misconceptions about LLMs and artificial intelligence. These words imply that these algorithms have human-like qualities; they seduce you into believing that algorithms display emergent behavior and are capable of more than they are truly capable of. At a fundamental level, this terminology is incorrect. A computer doesn’t learn in any way similar to how humans learn. Models do improve based on data and feedback, but it is incredibly important to keep this mechanistically distinct from anything like human learning. Indeed, you probably do not want an AI to learn like a human: we spend many years of our lives focused on education and still make dumb decisions.

</details>

深度学习算法的训练方式远比人类学习更为公式化。这里的"公式化"既指字面意义上大量使用数学公式，也指比喻意义上遵循简单重复的过程，重复数十亿次直至完成。我们不会深入数学细节，但本章将帮助您揭开大语言模型训练的神秘面纱。

许多机器学习算法使用名为梯度下降的训练算法。这个算法的名称本身就暗示了一些细节，我们将通过梯度下降在机器学习中应用的高层概述来回顾这些细节。一旦您理解了训练多种不同模型类型的通用方法，我们将探讨如何将梯度下降应用于大语言模型，以创建能够生成令人信服文本输出的模型。

理解这些细节将帮助您避免"学习"等词语带来的不准确联想。更重要的是，它还将让您更好地理解大语言模型在当前设计下成功与失败的原因，以及这些算法可能以微妙方式产生误导性输出的常见情况。

<details>
<summary>英文原文</summary>

Deep learning algorithms train in a way that is far more formulaic than how humans learn. It is formulaic in the literal sense of using a lot of math and the figurative meaning of following a simple repetitive procedure billions of times until completion. We will spare you the math, but in this chapter, we will help you remove the mystery of how LLMs are trained.

Many machine learning algorithms use the training algorithm called gradient descent. The name of this algorithm implies some details that we’ll review with a high-level overview of how gradient descent is used for machine learning. Once you understand the general approach used to train many different model types, we will explore how gradient descent is applied to LLMs to create a model that produces convincing textual output.

Understanding these details will help you avoid inaccurate connotations implied by words like learn. More importantly, it will also prepare you to understand better when LLMs succeed and fail in their current design and the often-subtle ways such algorithms can produce misleading outputs.

</details>

### 4.1 梯度下降

梯度下降是所有现代深度学习算法的关键。当行业从业者提到梯度下降时，他们实际上指的是训练过程中的两个关键要素。第一个是损失函数，第二个是计算梯度——这些度量告诉你如何调整神经网络的参数，使得损失函数以特定方式产生结果。你可以将这两者视为两个高级组件：

<details>
<summary>英文原文</summary>

Gradient descent is the key to all modern deep-learning algorithms. When an industry practitioner mentions gradient descent, they are implicitly referring to two critical elements of the training process. The first is known as a loss function, and the second is calculating gradients, which are measurements that tell you how to adjust the parameters of the neural network so that the loss function produces results in a specific way. You can think of these as two high-level components:

</details>

- 损失函数——你需要一个单一数值评分，计算算法运行得有多差。

- 梯度下降——你需要一个机械过程，调整算法内部的数值，使损失函数得分尽可能小。

<details>
<summary>英文原文</summary>

Loss function—You need a single numeric score that calculates how poorly your algorithm works.

Gradient descent—You need a mechanical process that tweaks the numeric values inside an algorithm to make the loss function score as small as possible.

</details>

损失函数和梯度下降是用于生成机器学习模型的训练算法的组成部分。如今有许多不同的训练算法在使用，但通常每种算法都会向模型输入数据，观察模型的输出，并对模型进行调整以提升其性能。训练算法会重复这一过程无数次。只要有足够的数据，模型在面对从未见过的输入时，就能反复稳定地产生预期的输出。

<details>
<summary>英文原文</summary>

The loss function and gradient descent are components of the training algorithm used to produce a machine learning model. Many different training algorithms are in use today, but generally, each algorithm sends inputs into a model, observes the model’s output, and tweaks the model to improve its performance. A training algorithm will repeat this process a tremendous number of times. Given enough data, a model will produce the expected outputs repeatedly and reliably when confronted with previously unseen input.

</details>

什么是损失函数？

我们将以赚钱为例，帮助建立对合适损失函数的直观认识。诚然，一个聪明人能赚钱，所以如果你有一台智能计算机，它也应该能帮你赚钱。为这一任务（或任何其他任务，这些原则适用于LLM之外的任何机器学习问题）选择合适的损失函数，需要满足三个标准：特异性、可计算性和平滑性。换句话说，损失函数需要……

<details>
<summary>英文原文</summary>

We will use the example of wanting to make money to help develop a mental picture of a suitable loss function. Indeed, an intelligent person can make money, so if you have an intelligent computer, it should be able to help you make money. To pick a suitable loss function for this or any other task (these lessons generalize to any ML problem beyond LLMs), we need to satisfy three criteria: specificity, computability, and smoothness. In other words, the loss function needs to be

</details>

具体且与模型期望行为相关；在合理的时间和资源条件下可计算；平滑，即对于相似输入，函数输出不会剧烈波动。我们将通过以下正例和反例来帮助您建立对每个属性的直觉。

<details>
<summary>英文原文</summary>

Specific and correlated with the desired behavior of the model Computable in a reasonable amount of time with a reasonable amount of resources Smooth, in the sense that the function’s output does not fluctuate wildly when given similar inputs We will use the following examples and counterexamples to help you develop an intuition for each property.

</details>

### 损失函数的具体性

首先，我们来举一个糟糕的具体性例子。如果老板对你说：“造一台智能计算机”，这固然是一个宏伟的目标，但并不是一个具体的目标。还记得第 1 章我们讨论过给智能下定义有多困难吗？你的老板到底想要这台计算机在哪些方面表现出智能？一台深谙世事却解不了微积分作业的计算机，能满足要求吗？相反，你可以尝试针对某个特定的智商分数进行优化，但那个分数和你老板想要的东西相关吗？早在 LLM 出现之前的十多年里[1]，我们就能让计算机通过智商测试了。然而，除了通过智商测试和执行有限任务外，它们什么都做不了。归根结底，智商测试与我们期望计算机做的事并不相关。因此，在机器学习中，或者为老板建造那台智能计算机时，将智商作为成功的度量标准并不值得优化。

另一个例子涉及管理金钱的挑战。考虑这样一个场景：你想要最小化你的债务。你甚至可能希望债务为负，也就是说别人欠你钱！我们在这里用债务为例，因为它本质上是一个你想要变得更小的值。这个类比与实践中的说法完美契合：你想要最小化你的损失，就像你想要减少你的债务一样。债务数额也是一个客观度量，使其成为确保损失函数在变化条件下依然相关的好方法。最后，如果我们的总体目标是保持现金盈余，那么最小化债务与该目标高度相关。最小化债务具备一个好的损失函数的所有特征！

<details>
<summary>英文原文</summary>

First, let’s start with a bad example of specificity. If your boss came to you and said, “Build an intelligent computer,” that would be a magnificent goal, but it is not a specific goal. Remember, in chapter 1, we discussed how difficult it is to define intelligence. What exactly does your boss want this computer to be intelligent at? Would a street-smart computer that cannot do your calculus homework suffice? Instead, you could try to optimize for a specific IQ score, but does that correlate with what your boss wants? We have been able to get computers to pass IQ tests for over a decade [1], even before the introduction of LLMs. However, they could not do anything other than pass an IQ test and perform limited tasks. Ultimately, the IQ test does not correlate with what we want computers to do. As a result, it is not worth optimizing IQ as a metric for success in machine learning or for building the intelligent computer your boss asked you to create. Another example involves the challenge of managing money. Consider a scenario where you want to minimize the debt you carry. You might even want your debt to go negative, meaning others owe you money! We use the example of debt here because it is intrinsically a value you want to make smaller. This analogy aligns perfectly with the terminology used in practice: you want to minimize your loss just as you want to reduce your debt. The volume of debt is also an objective measure, making it a good way of ensuring our loss function is relevant under changing conditions. Finally, if our overall goal is to maintain a surplus of money, minimizing debt correlates well with that goal. Minimizing debt has all of the characteristics of a good loss function!

</details>

术语说明：你可能会听到损失函数也被称为目标函数。我们建议新手避免使用这个术语，因为它含义模糊。例如，不清楚你是想最小化（债务）还是最大化你的目标（利润）。这两种方法在技术上都可行；将最大化目标乘以-1，就得到了最小化目标。

<details>
<summary>英文原文</summary>

A note on terminology You may also hear loss functions described as objective functions. We recommend avoiding this term as a newcomer because it is ambiguous. For example, it is unclear whether you want to minimize (debt) or maximize your objective (profit). Both approaches technically work; multiply a maximizing objective by −1, and you now have a minimizing objective.

</details>

你可能也会在某些语境中听到奖励函数这一术语，例如强化学习（RL）。这很恰当，因为强化学习算法通过执行期望的行为来最大化奖励。

<details>
<summary>英文原文</summary>

You may also hear the term reward function used in some contexts, such as reinforcement learning (RL). This is appropriate because RL algorithms seek to maximize reward by performing a desirable behavior.

</details>

无论术语如何，目标函数、奖励函数和损失函数都满足同一个基本需求：它们提供了一种评估机器学习模型输出结果的方式。

<details>
<summary>英文原文</summary>

Regardless of the terminology, objective functions, reward functions, and loss functions all address the same fundamental requirement: they provide a way of evaluating the outputs that a machine learning model produces.

</details>

### 损失函数的可计算性

损失函数还必须能够由计算机快速计算。债务示例在这方面并不合适，因为所需的全部输入和输出并非计算机能轻易获取。在工作中更努力会增加收入从而降低债务吗？也许吧，但我们如何将你的努力工作编码进计算机呢？这里我们面临一个问题：最小化债务最关键的因素难以量化，比如工作机会、你与这些工作的匹配度、晋升可能性等。因此，损失是具体的，但与损失相关的输入却是不可计算的。

一个更好、更可计算的目标是预测投资的损失。这个目标更好的原因很微妙。该目标仍然是客观的，因为我们的算法从历史数据中学习。例如，历史上对债券X和股票Y的投资有特定的回报。输入现在也变得客观：你可以量化投入到每项投资中的现金金额。你要么投入资金，要么取出资金。没有像“努力工作”这样难以编码的问题需要处理。有了历史数据的副本，计算机可以快速计算投资的损失/回报。

<details>
<summary>英文原文</summary>

The loss function must also be something we can compute quickly with a computer. The debt example is unsuitable for this aspect because all the inputs and outputs you need are not readily available to a computer. Will working harder at your job increase your income and thus lower your debt? Maybe, but how will we encode your hard work into the computer? Here, we have the problem that the most critical factors to minimizing debt are hard to quantify, like job availability, your fit for such jobs, likelihood of promotion, etc. So the loss is specific, but the inputs that connect to that loss are not computable. A better, more computable goal would be to predict the loss on an investment. The reasons this goal is better are subtle. The goal is still objective because our algorithms learn from historical data. For example, a historic investment in bonds X and stocks Y had certain returns. The inputs are also now objective: you can quantify the amount of cash you put into each investment. You either put money in, or you took it out. There are no hard-to-encode problems like “hard work” to deal with. With a copy of historical data, a computer can quickly calculate the loss/return on an investment.

</details>

### 损失函数的平滑性

我们需要考虑的第三个特性是平滑性。很多人通过对比光滑与粗糙的纹理，就能直观理解平滑的含义。不过此处讨论的是函数的平滑性，可以通过绘制函数图像来呈现。例如，在预测投资损失时，我们会遇到投资收益通常不平滑的问题。它们可能呈现出波动模式，价格图线锯齿状起伏，充满尖锐突变。这给学习带来了困难。图4.1展示了真实世界投资收益的不稳定值曲线。

<details>
<summary>英文原文</summary>

The third thing we need is smoothness. Many people have good intuition for what smoothness means by thinking about a smooth versus bumpy texture. Instead of texture, we’re talking about the smoothness of a function, which can be depicted by drawing that function as a graph. For example, when trying to predict a loss on an investment, we run into the problem that investment returns are not usually smooth. They may follow a pattern of volatility where price graphs are jagged with sharp, sudden changes. This makes learning difficult. A graph showing the unstable values of real-world investment returns is shown in figure 4.1.

</details>

![图 4.1 投资回报难以预测，部分原因在于其不平滑。(Image modified from [2] under the Creative Commons license )](assets/fig-4-1-c011ee570d.jpg)

*图4.1 投资回报难以预测，部分原因在于其不平滑。(Image modified from [2] under the Creative Commons license )*

对于任何预测方法而言，不可预测的行为都是个问题。你最好始终对那些声称能很好预测此类非平滑数据的人或方法保持警惕。然而，“平滑”有一个精确的技术定义，如果损失函数不满足这一定义，那将是一个难以逾越的障碍。依赖于不连续性或函数值一致性的断裂的函数，是最常见的在技术上不平滑的函数，但我们希望在实践当中仍能使用它们。图 4.2 给出了一些非平滑函数的例子，以帮助你理解。平滑性通常因不连续性（如中间的图所示）或函数值的突变（如右图所示）而受阻。

<details>
<summary>英文原文</summary>

erratic behavior is problematic for any predictive approach. It would be best if you were always cautious of anyone or any approach that claims to work well in predicting nonsmooth data like this. However, there is a precise technical definition of smooth that, if not satisfied by a loss function, is a hard deal-breaker. Functions that depend on discontinuities, or breaks in the consistency of their values, are the most common functions that are not technically smooth, but we would like to be able to use them in practice. Some examples of nonsmooth functions are shown in figure 4.2 to help you understand. Smoothness is usually inhibited due to discontinuities, such as that shown in the center graph, or distinct changes in the value of a function, as shown in the graph on the right.

</details>

我们将不深入探讨描述平滑性以及平滑函数中可接受或不可接受的值变化的形式数学定义。不过，我们已经提供了足够的背景知识，让你了解你需要知道的内容。你需要理解的关键点是，你对平滑的直觉（即值连续变化）能很好地衡量损失函数的可行性。这看起来可能有些随意，但这是一个普遍存在的问题。假设你想构建一个能准确预测癌症的模型。准确率不是一个平滑函数，因为你计算的是成功预测数量占总预测数量的比例。例如，如果你有50名患者，正确预测了其中48名，那么平滑函数会允许48.2例、47.921351例或任何你能想到的数字。然而，实际癌症病例数只能取整数1, 2, 3, ..., 48, 49, 50，因为不存在部分病例这种情况。

<details>
<summary>英文原文</summary>

We won’t go deep into the formal mathematical definitions that describe what makes something smooth and what value changes are acceptable or unacceptable in smooth functions. Still, we’ve given you enough background to understand what you need to know. The important thing for you to understand is that your intuition of what smooth means, that the value changes continuously, is a good barometer for how viable a loss function is. This may seem arbitrary, but it is an ubiquitous problem. Say you want to build a model to predict cancer accurately. Accuracy is not a smooth function because you count the number of successful predictions out of the total predictions. For example, if you had 50 patients and predicted 48 of them correctly, a smooth function would have an option for 48.2 cases, 47.921351 cases, or any number you might think of. However, the actual count of cancer cases is constrained to the integers 1, 2, 3, . . ., 48, 49, 50 because there is no such thing as a partial case of cancer.

</details>

![图 4.2 左侧是平滑函数的示例，右侧是两个非平滑函数的示例。中间的例子大部分是平滑的，但有一个区域不平滑，因为函数在该处没有值。右侧的函数由于值的剧烈变化，在任何地方都不平滑。](assets/fig-4-2-cc90c84d81.png)

*图4.2 左侧是平滑函数的示例，右侧是两个非平滑函数的示例。中间的例子大部分是平滑的，但有一个区域不平滑，因为函数在该处没有值。右侧的函数由于值的剧烈变化，在任何地方都不平滑。*

如何处理非平滑损失？

<details>
<summary>英文原文</summary>

How do you handle nonsmooth losses?

</details>

准确率是最常见的预测目标之一，但我们无法在训练算法时使用它，这可能会令人震惊。但这是事实！那么，我们如何处理这个奇怪的现象？答案是创建一个代理问题。代理问题是表示问题的另一种方式，与我们要解决的问题相关但表现更好。在这种情况下，我们使用交叉熵损失函数而不是准确率。虽然我们不会在这里深入探讨交叉熵损失的细节，但它的使用表明代理问题是机器学习和人工智能中使用的核心技巧。

<details>
<summary>英文原文</summary>

It may be shocking that accuracy is one of the most common predictive goals, but we cannot use it when training an algorithm. But it is true! So how do we handle this strange phenomenon? The answer is to create a proxy problem. A proxy problem is an alternate way of representing a problem that correlates with what we want to solve but is better behaved. In this case, we use a cross-entropy loss function instead of accuracy. While we won’t go into the details of cross-entropy loss here, its use demonstrates that proxy problems are fundamental tricks used in machine learning and artificial intelligence.

</details>

这一讨论引出了关于大语言模型学习方式的另一个关键启示，这一启示也适用于大多数算法：我们用来训练模型的技术，其关注点往往并非我们期望模型完成的任务，而是我们能够促使模型学习的内容。这种关注点可能导致激励不匹配，进而引发意外结果或性能低下。在考察第二个主要训练组件——梯度下降——之后，我们将讨论大语言模型损失函数的本质是如何造成这种激励不匹配的。

<details>
<summary>英文原文</summary>

This discussion leads us to another critical takeaway about how LLMs learn, which is true of most algorithms: the technique we use to train them is not always focused on what we want them to do but on what we can make them learn. This focus can lead to an incentive mismatch, leading to unexpected results or low performance. We will discuss how the nature of an LLM’s loss function creates this incentive mismatch after examining the second major training component: gradient descent.

</details>

### 4.1.2 什么是梯度下降？

拥有损失函数是执行梯度下降的前提。损失函数客观地告诉你当前任务执行得有多糟糕。

梯度下降是我们用来调整神经网络参数以减少损失的过程。

这是通过利用损失函数比较输入训练数据以及神经网络的实际输出与期望输出来实现的。

在这种情况下，梯度是你需要改变神经网络参数的方向和大小，以减少损失函数衡量的误差。

梯度下降向我们展示了如何“微调”神经网络的所有参数，以提高其性能并缩小期望输出与实际输出之间的差距。

图4.3展示了这一过程的示意图。

<details>
<summary>英文原文</summary>

Having a loss function is a prerequisite for performing a gradient descent. The loss function tells you objectively how poorly you are performing the task. Gradient descent is the process we use to figure out how to tweak the parameters of the neural network to reduce the loss incurred. This is done by comparing the input training data and the actual versus expected outputs of the neural network using the loss function. In this case, the gradient is the direction and amount that you need to change the parameters of a neural network to reduce the amount of error measured by the loss function. Gradient descent shows us how to tweak all the parameters of a neural network “just a little bit” to improve its performance and reduce the difference between the expected and actual outputs. A diagram of this process is shown in figure 4.3.

</details>

![图 4.3 在梯度下降过程中，输入和标签（每个输入的已知正确答案）用于调整神经网络。网络由参数组成，每次应用梯度下降时，这些参数都会发生微小变化。通过应用数百万或数十亿次梯度下降，我们最终将网络转变为有用的东西。](assets/fig-4-3-eef702dfaf.png)

*图4.3 在梯度下降过程中，输入和标签（每个输入的已知正确答案）用于调整神经网络。网络由参数组成，每次应用梯度下降时，这些参数都会发生微小变化。通过应用数百万或数十亿次梯度下降，我们最终将网络转变为有用的东西。*

如图4.3所示，每次应用梯度下降时，我们都会创建一个略有不同的新网络。由于变化很小，这个过程需要重复数十亿次。这样，所有微小的变化累积起来，最终使整个网络发生更显著、更具意义的改变。

<details>
<summary>英文原文</summary>

As figure 4.3 shows, we create a new, slightly different network every time we apply gradient descent. Because the changes are small, this process has to be performed billions of times. This way, all the small changes add up to a more significant, mean-ingful change in the overall network.

</details>

### 现代注释

LLM之所以进行数十亿次参数更新，是因为它们接受了数十亿个token的训练。数据越多，梯度下降运行的次数就越多。数据越少，需要运行的次数就越少。用于训练LLM的数据量超过你一生能阅读的总和。

<details>
<summary>英文原文</summary>

LLMs perform billions of parameter updates because they are trained on billions of tokens. The more data you have, the more times you run gradient descent. The less data you have, the less often you need to run it. The data used to train an LLM is more than you could read in a lifetime.

</details>

梯度下降是一种重复应用且毫无偏差的数学过程。

无法保证它一定能奏效，或找到最佳甚至不错的解。尽管如此，许多研究者都惊讶于这个相对简单的方法竟然如此实用。

为了帮助你理解梯度下降的工作原理，我们将用一个滚球下山的简单例子来说明。小球的位置代表神经网络中某个节点的参数值，该参数可由训练算法调整。山的高度代表损失值，衡量模型在处理训练输入时表现有多糟糕。我们希望将小球滚下山坡，进入最深的山谷，因为那里损失最低，表明模型表现最佳。图4.4展示了这一示例。

<details>
<summary>英文原文</summary>

Gradient descent is a mathematical process that is applied repeatedly without deviation. There are no guarantees that it will work or find the best or even a good solution. Nevertheless, many researchers have been surprised by how practical this relatively simple approach is.

To help you understand how gradient descent works, we will use a simple example of rolling a ball down a hill. The ball’s location represents a parameter value for a node in the neural network that the training algorithm can alter. The hill’s height is the amount of loss and describes how poorly the model performs for the training input. We want to roll the ball down the hill into the deepest valley because that is the area with the lowest loss, which indicates that the model is performing its best. An example of this is shown in figure 4.4

</details>

![图 4.4 展示了针对单参数问题的梯度下降整体情况。曲线表示给定参数值下的损失函数值。小球的位置表示当前参数值下的损失。目标是找到全局最小值对应的参数值，即损失最小的理想解。](assets/fig-4-4-ab19356453.png)

*图4.4展示了针对单参数问题的梯度下降整体情况。曲线表示给定参数值下的损失函数值。小球的位置表示当前参数值下的损失。目标是找到全局最小值对应的参数值，即损失最小的理想解。*

如你所见，小球可能落入许多山谷。行业术语称此问题为非凸优化，因为有多条路径可降低损失，但并非每条路径都必然通向最优解。还需注意，这并非比喻。梯度下降确实是按这种方式看待问题的。这些示例展示了梯度下降如何在一个参数模型中工作。在训练LLM时，同样的过程应用于数十亿个参数。

<details>
<summary>英文原文</summary>

As you can see, the ball could fall into many valleys. The industry jargon would be to call this problem nonconvex because multiple paths lead to reduced loss, but each path does not necessarily progress toward the best possible solution. It is also important to note that this is not an analogy. Gradient descent literally looks at the world this way. These examples show how gradient descent works for a model with one parameter to optimize. The same procedure is applied to billions of parameters when training an LLM.

</details>

因此，从这个位置出发，我们贪婪地判断哪个方向能让球沿坡下行。我们在图4.5中应用了两次梯度下降。这表明贪婪的选择是向左。当我们通过调整参数向左移动时，球会沿斜坡略微下降。从图中可以看出，向右搜索存在更优的解，但由于算法过于简单，梯度下降不太可能找到它。在这种情况下，要找到最优解需要更智能的搜索与探索策略，但实践中成本过高，难以有效执行。

<details>
<summary>英文原文</summary>

So from this position, we greedily look at which direction to move the ball downhill. We apply gradient descent two times in figure 4.5. This shows that the greedy option is to the left. When we move to the left by adjusting our parameter, we slightly move the ball down the slope. From the graph, you can see that a better solution exists by searching to the right, but due to the algorithm’s simplicity, it is unlikely that gradient descent will find it. Finding the optimal result in this case would require a more intelligent strategy involving searching and exploration, which is too costly to do well in practice.

</details>

![图 4.5 梯度下降算法通过迭代调整参数，以最小损失寻找最优结果。不幸的是，该算法会陷入局部最小值——图中一个并非最优的区域，因为其他参数值对应着损失更低的区域。](assets/fig-4-5-be9aa69f44.png)

*图4.5 梯度下降算法通过迭代调整参数，以最小损失寻找最优结果。不幸的是，该算法会陷入局部最小值——图中一个并非最优的区域，因为其他参数值对应着损失更低的区域。*

另外，请注意在图4.5的第二步中，小球卡住了。虽然显然继续向左移动会实现更低的损失，但这个结果之所以显而易见，只是因为我们能看到全局。梯度下降既无法看到全局，也无法看到附近的情况。它只知道由当前参数和损失函数决定的精确位置。因此，它是一种贪婪过程。像梯度下降这样的贪婪过程是简化方法，具有一个理想的特性——可计算性，即多次运行以达到结果的计算成本并非高得令人望而却步。贪婪过程是短视的，因为它们仅基于当前状态选择下一步最优步骤，尽管可能存在更广泛、更优的解决方案。它们之所以这样做，是因为评估当前及所有可能的未来状态是不可能的，因为需要考虑的潜在结果数量太多。那将需要过多的计算。人们希望，利用有限信息做出许多简单的局部最优决策，通常能带来最积极的结果——在此案例中，即最小化损失函数的值。

<details>
<summary>英文原文</summary>

Also, notice that in the second step in figure 4.5, the ball gets stuck. While it is evident that continuing to move to the left will achieve an even lower loss, this result is only obvious because we can see the whole picture. Gradient descent cannot see the entire picture or even what is nearby. It only knows the exact location due to the current parameters and the loss function. Hence, it is a greedy procedure. Greedy procedures such as gradient descent are simplified approaches with the desired property of computability in that they are not prohibitively expensive to run many times to achieve an outcome. Greedy procedures are short-sighted because they choose the next optimal step based only on the current state, although broader, more optimal solutions may exist. They do this because evaluating the current and all possible future states would be impossible due to the number of potential outcomes that need to be considered. It would simply be too much to compute. The hope is that making many simple optimal decisions using limited information will generally lead to the most positive outcome—in this case, minimizing the value of the loss function.

</details>

### 梯度下降中的重要细微差别

在这部分关于梯度下降的讨论中，我们跳过了一些在实际应用中需要考虑的重要细节。首先，如这里所述，梯度下降需要同时使用所有训练数据，这在计算上不可行。相反，我们使用一种称为随机梯度下降（SGD）的过程。SGD与我们描述的过程完全相同，只是它使用训练数据的一个小随机子集，而不是整个数据集。这大大减少了训练模型所需的内存，从而得到更快、更好的解决方案。这种方法之所以有效，是因为梯度下降只在当前贪心方向上做微小变化。事实证明，在决定下一步走向时，少量数据几乎与使用全部数据一样好。如果你有十亿个token，你可以在大约相同的时间内进行十亿次SGD步骤，而使用全部数据的一次标准梯度下降步骤也需要这么多时间。

许多训练方法使用SGD的一种特殊形式，称为自适应矩估计（Adam）。Adam包含一些额外的技巧，有助于更快地最小化损失函数并避免陷入停滞。Adam的主要技巧是给球一些动量，随着更新不断沿一个方向移动，动量会累积。这种动量使球更快地滚下山坡，并且意味着如果遇到一个小的局部最小值，可能有足够的动量冲过去并继续前进，从而到达损失函数图中损失最小的区域。

Adam的缺点是，存储每个参数的动量信息会使训练所需的内存比普通SGD增加三倍。内存是构建LLM时最关键的因素，因为它通常决定了你需要多少块GPU，这直接转化为你口袋里的钱。尽管Adam不会使最终模型变大（因为训练完成后你可以丢弃与Adam额外动量计算相关的数据），但你首先需要一个足够大的系统来执行训练。Adam能够更有效地最小化损失所带来精度提升，伴随有明显的代价。

<details>
<summary>英文原文</summary>

In this discussion of gradient descent, we have skipped some important nuances that need to be considered for real-world use. First, as described here, gradient descent would need to use all of the training data simultaneously, which is computationally infeasible. Instead, we use a procedure called stochastic gradient descent (SGD). SGD is precisely the same as we’ve described, except it uses a small random subset of the training data instead of the entire dataset. This dramatically reduces the memory required to train the model, resulting in faster, better solutions. This method works because gradient descent only makes small changes in the current greedy direction. It turns out that a little data is almost as good as using all the data when figuring out which step to take next. If you have a billion tokens, you can take a billion SGD steps in about the same amount of time it takes to do one standard gradient descent step using all the data. Many training approaches use a particular form of SGD called Adaptive Moment Estimation (Adam). Adam includes some extra tricks to help minimize the loss function faster and avoid getting stuck. Adam’s main trick is that it gives the ball some momentum, which builds as updates continually move in one direction. This momentum causes the ball to roll down the hill faster and means that if a small local minimum is hit, there might be enough momentum to plow past that point and continue onward, thus reaching the area of the loss function graph with the smallest amount of loss. The downside of Adam is that storing this information about momentum for each parameter increases the memory required for training by a factor of three compared to plain SGD. Memory is the most critical factor when building LLMs because it often determines how many GPUs you need, translating to cash out of your pocket. Although Adam won’t make the final model larger because you can throw away the data related to Adam’s extra momentum calculations once you are done training, you still need a system large enough to perform the training in the first place. The increased accuracy that comes with Adam’s ability to minimize loss more effectively comes with a distinct price.

</details>

### 4.2 LLM 学习模仿人类文本

现在我们理解了深度学习算法是如何通过指定损失函数并使用梯度下降进行训练的，接下来可以讨论如何将其应用于 LLM。具体而言，我们将重点关注训练 LLM 所用的数据以及损失函数或奖励函数。LLM 通常是在人类撰写的文本上进行训练的。

具体来说，它们被明确训练为模仿人类生成的文本。尽管这听起来有点显而易见（不然还能训练它们做什么呢？），但这一细节常常被忽视或与其他概念混淆，即使是该领域的专家也不例外。尤其是，语言模型并非被训练来做以下任何一件事：

<details>
<summary>英文原文</summary>

Now that we understand how deep learning algorithms are trained by specifying a loss function used with gradient descent, we can discuss how this is applied to LLMs. Specifically, we will focus on the data and loss or reward functions used to train LLMs. LLMs are generally trained on human-authored text. Specifically, they’re explicitly trained to mimic texts produced by humans. While this sounds a bit obvious (what else would they be trained to do?), this detail is commonly missed or confused with other things, even by experts in the field. In particular, language models are not trained to do any of the following things:

</details>

记忆文本、生成新想法、构建世界表征、产生事实准确的文本。在深入之前，有必要进一步解释这个概念。训练一个模型下棋时，模型会因为获胜得到奖励而学会好好下棋。相比之下，语言模型只有在生成与训练数据完全相同的文本时才能获得奖励。因此，LLM生成的所有与训练语料中文本相似的文本都会带来高奖励（或低损失），即使这些生成内容并非真实或事实。这是4.1节讨论的损失函数与设计者更高层目标之间不一致的一个例子。

LLM是在从互联网抓取的数百GB文本数据集上训练的。互联网以包含大量错误（且奇怪）的信息而闻名。在大多数任务上表现更好的LLM，往往在那些训练数据中常被错误表述的任务上表现更差（参见 https://github.com/inverse-scaling/prize 上的逆缩放奖）。例如，研究人员一致发现，更好的语言模型也更擅长复述常见的错误知识[3]、模仿刻板印象和社会偏见[4]。它们容易陷入不断强化错误的恶性循环。例如，在生成包含错误的代码后，它们更有可能生成包含更多错误的代码[5]。这些内容在训练文本中普遍存在，因此LLM预测它们（尽管是错误的）也会获得正向奖励。因此，基于其损失函数变得更好的LLM，也意味着在需要真实性和正确性的这些任务上变得更差。

<details>
<summary>英文原文</summary>

Memorize text Generate new ideas Build representations of the world Produce factually accurate text It is essential to explain this notion further before we go deeper. When one trains a model to play chess, the model learns to play well because it gets rewarded for winning. A language model, by contrast, only gets rewarded for producing text that looks exactly like the training data. Consequently, all text generated by the LLM that looks like text in the training corpus produces high rewards (or low loss), even when those generations are not truthful or factual. This is an example of misalign-ment between the loss function and the designer’s higher-level goal, as discussed in section 4.1.

LLMs are trained on datasets of hundreds of gigabytes of text scraped from the internet. The internet is famous for containing a large amount of incorrect (and weird) information. LLMs that are better at most tasks often end up being worse at tasks that are commonly misrepresented in their training data (see the Inverse Scaling Prize at https://github.com/inverse-scaling/prize). For example, researchers have consistently found that better language models are also better at reproducing common knowledge that is false [3], mimicking stereotypes and social biases [4]. They tend to fall into a downward spiral that reinforces errors. For example, after generating code that contains bugs, they’re more likely to generate code that contains additional bugs [5]. These things are commonly represented in the training text, so LLMs are positively rewarded for predicting them even though it’s wrong. Thus, getting better based on its loss function for an LLM also means getting worse at these tasks that require truth and correctness.

</details>

### 4.2.1 LLM 的奖励函数

之前我们提到，LLM会因为生成“看起来像训练数据”的数据而获得奖励。在本小节中，我们将更具体地探讨这意味着什么。

LLM的训练方式是向模型展示句子的前几个词元，然后让它预测下一个词元。

损失函数基于该预测与训练数据相比的准确性。

例如，模型可能看到“This is a”，并期望生成“test”。如果模型生成“test”，则得分；如果没有，则扣分。

这一过程对文本的所有起始片段都执行，如图4.6所示。

在这里，模型被训练独立预测每个高亮词。

这种设置并非LLM独有，它已被用于训练循环神经网络（RNN）多年。

然而，LLM变得如此流行的一个重要原因是，它们的训练效率远高于RNN。

RNN必须按顺序对每个生成结果进行训练，因为每个新生成的词依赖于之前选择的词。

而LLM由于第3章讨论的transformer架构，可以并行训练所有生成结果。

并行训练相关生成结果的能力带来了巨大的速度提升，使得大规模训练成为可能，也是构建当今使用TB级数据的最先进LLM的前提。

我们之前讨论过预测下一个词元可能带来的问题，因为算法可能会被激励产生不正确或事实错误的输出。

我们还必须探讨为什么尽管存在这些问题，这种方法仍能产生如此令人信服的输出的直觉。

一个合理的问题是：一个被训练来生成最可能的下一个词元的算法，如何能看起来像是在进行推理？

<details>
<summary>英文原文</summary>

Previously, we said that LLMs are rewarded for producing data that “looks like its training data.” In this subsection, we will explore what this means more concretely. LLMs are trained by being shown the first couple of tokens of a sentence and having it predict the next token. The loss is based on the accuracy of that prediction compared to the training data. For example, it might be shown “This is a” and be expected to produce “test.” If the model produces “test,” it gets a point, and if it does not, it loses a point. This process is done for all beginning segments of the text, as shown in figure 4.6. Here, it is trained to predict each of the highlighted words independently. This setup is not unique to LLMs. It has been used to train recurrent neural networks (RNNs) for many years. However, an essential part of why LLMs have become so popular is that they can be trained much more efficiently than an RNN. An RNN must be trained on each generation sequentially because each newly generated word depends on the prior words chosen. An LLM can be trained on all generations in parallel due to the transformer architecture discussed in chapter 3. The ability to train a model on related generations in parallel represents a massive speed-up, allowing training at a large scale, and is a prerequisite for building today’s state-of-the-art LLMs using terabytes of data. We discussed how predicting the next token can be problematic because the algorithm may be incentivized to produce incorrect or factually errant outputs. We must also discuss the intuition behind why, despite this, this approach can produce such convincing outputs. It is reasonable to ask: How can an algorithm trained to create the next most likely token seemingly perform something we could mistake for reasoning?

</details>

![图 4.6 一个LLM会把这个句子看九次，每次从九个序列末尾的单个词预测中学习。](assets/fig-4-6-9238774812.jpg)

*图4.6 一个LLM会把这个句子看九次，每次从九个序列末尾的单个词预测中学习。*

为培养这种直觉，想象一下你如何尝试预测给定句子的下一个词元。计算机无需快速响应，所以你可以慢慢思考。考虑句子“I love to eat <blank>”，并尝试猜测<blank>处应填什么词。句子前半部分提供了有价值的上下文。既然我们在讨论吃，你几乎可以立即将范围缩小到食物。计算机维护所有可能食物的列表并不困难。

现在，如果你考虑本书作者的背景，你会获得更多上下文。我们是同一地理区域的美国人，这使得某些特定菜系比其他菜系更可能出现。大型语言模型不会拥有这些背景信息，但如果句子更长且有更多上下文，你可以开始像图4.7所示那样缩小选择范围。

<details>
<summary>英文原文</summary>

To develop this intuition, imagine how you might try to predict the next token for a given sentence. A computer has no pressure to respond quickly, so take your time. Consider the sentence “I love to eat <blank>,” and try to guess what word might go into the <blank>. The earlier parts of the sentence give you valuable context. Since we are discussing eating, you can almost immediately narrow the scope to a food item. Keeping a list of all possible food items is not difficult for a computer. Now if you consider the background of the authors of this book, you will have even more context. We are Americans in a common geographical area, which makes specific cuisines more likely than others. An LLM will not have this background, but if the sentence was longer and had more context, you could start to narrow down the choices in the same way as shown in figure 4.7.

</details>

![图 4.7 上下文可以帮助你合理预测下一个词。从左向右移动时，句子中可能出现的额外文本不断增加。每个句子气泡中的图像展示了增加的上下文如何排除预测。](assets/fig-4-7-5608d47f5b.png)

*图4.7 上下文可以帮助你合理预测下一个词。从左向右移动时，句子中可能出现的额外文本不断增加。每个句子气泡中的图像展示了增加的上下文如何排除预测。*

当你在前文中识别出关键词或短语时，就能洞察到下一个最可能预测的词。计算机执行这些计算时所做的处理远超人类所需。这种暴力关联主要将范围缩小到非常合理的选项上。同样，模型会经过数十亿次更新来优化这些关联，从而获得一种有用的能力，这种能力与我们的目标——即让算法能够理解和响应人类文本——密切相关。

然而，相关不等于因果，这种下一个词预测策略可能会导致有趣的错误。大语言模型容易犯“循环论证”错误，即问题的前提暗示了某种不真实的东西。由于大语言模型不是为准确性或矛盾检测而训练的，它会试图生成一系列类似人类的文本预测，这些预测可能跟随你误导性的问题。图4.8给出了 ChatGPT 处理此类问题的一个例子，我们询问了干意大利面条的异常强度。

<details>
<summary>英文原文</summary>

As you identify keywords or phrases in the preceding text, you can gain insight into the best word to predict next. A computer performing these calculations does far more processing than a human requires. This kind of brute-force association mainly narrows the scope to something very reasonable. Again, the model will be updated billions of times to refine these associations and thus acquire a useful capability correlated with our goals of an algorithm able to understand and react to human text.

However, correlation is not causation, and the next-word prediction strategy can lead to humorous errors. LLMs are susceptible to a “begging the question” error, where the premise of the question implies something untrue. Since the LLM is not trained for accuracy or contradiction, it attempts to produce a sequence of human-like text predictions that might follow your misleading question. An example of ChatGPT struggling with this kind of problem is given in figure 4.8, where we ask about the exceptional strength of dry spaghetti.

</details>

![图 4.8 虽然预测下一个词元很强大，但它并不能赋予网络推理或逻辑能力。如果我们问ChatGPT一些荒谬不真实的事情，它会愉快地解释它是如何发生的。](assets/fig-4-8-ee055af12f.jpg)

*图4.8 虽然预测下一个词元很强大，但它并不能赋予网络推理或逻辑能力。如果我们问ChatGPT一些荒谬不真实的事情，它会愉快地解释它是如何发生的。*

意大利面能支撑自身上百倍重量这一核心说法是荒谬且不真实的。然而，算法通过将问题格式化为“为什么X如此坚固？”，已经预先准备好提供关于材料抗拉强度的答案。模型可以提取这一关键上下文。先前的训练数据很可能是基于一个事实性问题来解释这类材料属性，这告诉模型预测一个类似的回答是合适的。句子的主语（意大利面）和宾语（10磅重物）被用来提供回复中的次要细节，而回复本身是通用的。

<details>
<summary>英文原文</summary>

The core of why spaghetti can support hundreds of times its own weight is absurd and untrue. However, the algorithm has been primed to provide an answer about material tensile strength by formatting the question: “Why is it that X is so strong?” The model can extract this key context. Previous training data likely explains such material properties based on a factual question, which informs the model predicting that a similar response is appropriate. The subject of the sentence (spaghetti) and object (10 lb. weight) are used to inform minor details of the response, which is otherwise generic.

</details>

### 4.3 LLM与新颖任务

自回归的下一词预测策略的本质，及其在训练过程中作为损失或奖励的使用，为我们提供了关于LLM生成回复的性质以及它们可能事实不准确的深刻见解。然而，它也揭示了为什么LLM在信息检索方面可以非常有效，作为一种比标准搜索引擎强大得多的关键词搜索。有一些方法可以围绕非事实性回复的局限性进行设计。例如，许多LLM方法会在生成输出中添加引用，以便能够快速验证用于生成文本的内容是否事实准确。LLM也可以作为一个有价值的回音板，一个半伙伴来碰撞想法，作为灵感和创造力的来源。关键是，这也帮助你理解一个应该避免使用LLM的关键情况，因为它们更容易出错——即新颖问题和任务。

LLM通常不擅长执行新颖任务。判断你的任务是否新颖可能相当具有挑战性，因为互联网是奇怪的。互联网上存在大量随机的东西，包括如何编程绘制鸭子和独角兽的比赛[6]。如果任务与之前见过的任务足够相似，或者与训练数据中的其他内容结构相似，你可能会得到看似合理的结果。这个结果可能非常有用，但随着你的任务与训练数据中的内容相比变得越来越独特，它的质量可能会下降。

例如，我们要求ChatGPT编写计算数学常数π(pi)的Python代码。这个任务并不新颖；网上存在大量类似的代码，ChatGPT忠实地为我们返回了正确的代码。

<details>
<summary>英文原文</summary>

The nature of the autoregressive, next-word prediction strategy and its use as a loss or reward during the training process gives us valuable insight into the nature of an LLM’s generated responses and how they can potentially be factually inaccurate. However, it also shows us why LLMs can be effective for looking up information, as a far more powerful keyword search than a standard search engine. There are ways to design around the limitations of nonfactual responses. For example, many LLM approaches add citations to the generated output so that it is possible to quickly verify that factually accurate content was used to produce the generated text. An LLM can also be a valuable sounding board, a pseudo-partner to bounce ideas off of as a source of inspiration and creativity. Critically, this also helps you understand a key case where you should avoid LLMs because they will be more likely to produce errors—novel problems and tasks.

LLMs are generally not good at performing novel tasks. Figuring out if your task is novel can be pretty challenging, as the internet is weird. Tons of random things exist on the internet, including competitions on how to programmatically draw ducks and unicorns [6]. If the task is sufficiently similar to one already seen before or structurally similar to other things in the training data, you may end up with something that appears reasonable. This result can be extremely useful, but it can degrade as your task becomes more unique compared to what exists in the training data. For example, we asked ChatGPT to write code that calculates the mathematical constant 휋(pi) in Python. This task is not novel; tons of code like this exists online, and ChatGPT faithfully returns the correct code for us.

</details>

*代码：清单4.1 ChatGPT用Python计算π*

```
def calculate_pi(terms):
pi = 0
for i in range(terms):
term = ((-1) ** i) / (2 * i + 1)
pi += term
pi *= 4
return pi
print(calculate_pi(1000000))
```

<details>
<summary>英文原文</summary>

Listing 4.1 ChatGPT calculating pi in Python

</details>

现在让我们迫使 ChatGPT 做一些不算太难的推断。我们让 ChatGPT 把这个函数翻译成 Modula-3 编程语言。这个任务不算太大的推断；Modula-3 是一种风格相似的编程语言，也是一种具有历史意义的编程语言，影响了当今几乎所有最流行语言的最终设计！然而，它过于晦涩难懂。如今你几乎找不到这种编程语言的例子，主要是在大学编译器课程中。下面的清单显示了 ChatGPT 还算合理的尝试。正如你可能已经从本章的上下文中预料到的，ChatGPT 犯了一些错误，在清单中已标出。

<details>
<summary>英文原文</summary>

Now let us force ChatGPT to do some not terribly challenging extrapolation. We asked ChatGPT to translate this function to the programming language Modula-3.

This task is not too big of an extrapolation; Modula-3 is a programming language with a similar style and a historically significant programming language that influenced the eventual design of almost all the most popular languages today! However, it is excessively esoteric. You can find very few examples of this programming language today, mainly in the context of university compiler classes. The next listing shows Chat-GPT’s reasonable attempt. As you may have been able to predict from the context of this chapter thus far, ChatGPT made some errors, marked in the listing.

</details>

*代码：清单 4.2
ChatGPT 在 Modula-3 中计算 π
MODULE CalculatePi;
缺少 EXPORTS Main;*

```
IMPORT IO, Real;
PROCEDURE CalculatePi(terms: INTEGER): REAL;
VAR
pi: REAL := 0.0;
i: INTEGER;
BEGIN
FOR i := 0 TO terms - 1 DO
pi := pi + ((-1.0) ** FLOAT(i)) / (2.0 * FLOAT(i) + 1.0);
** isn’t a
END;
RETURN 4.0 * pi;
END CalculatePi;
BEGIN
IO.PutReal(CalculatePi(1000000), 0, 15);
END CalculatePi.
```

<details>
<summary>英文原文</summary>

Listing 4.2 ChatGPT calculates pi in Modula-3 MODULE CalculatePi; Missing EXPORTS Main;

</details>

这个简短的程序有三个错误，会导致它无法运行。更有趣的是，ChatGPT 之所以会犯这些错误，是因为它自信地从其他语言中推断出标准编码实践。（在这种情况下，“自信”意味着 ChatGPT 没有警告我们它可能出错。一位作者喜欢说，ChatGPT 听起来像他们最过于自信且经常出错的朋友。）在这个例子中，`**` 是一个常用的幂运算函数，所以 ChatGPT 认为 Modula-3 支持这个操作。据我们搜索互联网所知，Modula-3 没有任何关于如何对变量进行幂运算的文档示例。由于大多数编程语言使用 `^`、`**` 或 `pow` 选项支持此操作，ChatGPT 就凭空推断出了一种。正确答案应该是，它必须先实现一个 pow 函数，然后用它来计算 pi。

提供给 PutReal 函数的参数则是另一个谜。我们最好的猜测是，15 对应于打印浮点值 15 位的推断，这是计算 pi 时的典型默认值。无论如何，这不是该函数的工作方式。

更重要的是，ChatGPT 在某些细微细节上是正确的，但仅限于那些能在互联网上找到且已有解释的部分（例如，需要使用 `FLOAT(i)`，以及使用 `4.0 * pi` 而不是 `4 * pi`）。没有互联网示例的任务正是 ChatGPT 出错的地方。

<details>
<summary>英文原文</summary>

This short program has three errors that would prevent it from working. It is more interesting that ChatGPT gets these wrong because it confidently extrapolates stan-dard coding practices from other languages. (In this case, confidently means that ChatGPT does not warn us of its potential errors. One of the authors likes to say that ChatGPT sounds like their most overconfident and often incorrect friend.) In this case, ** is a commonly used exponentiation function, so ChatGPT decides that Modula-3 supports this operation. As far as we can tell from scouring the internet, Modula-3 has no documented example of how to exponentiate a variable. Because most programming languages support this action with a ^, **, or pow() option, Chat-GPT just extrapolates one into existence. The correct answer would be that it must first implement a pow function and then use it to compute pi. The arguments provided to the PutReal function are another mystery. Our best guess is that the 15 corresponds to an extrapolation of printing out 15 digits of a floating-point value, a typical default when calculating pi. Regardless, it is not how that function works.

The more significant point is that ChatGPT gets some of the nuanced details right but only for the parts that can be found on the internet and are already explained (e.g., FLOAT(i) is required, as is doing 4.0 * pi instead of 4 * pi). The tasks without examples on the internet are the ones where ChatGPT makes errors.

</details>

这个例子也凸显了当前LLM中“推理”感知与实际表现之间的局限性。Modula-3的完整语言规范可在网上获取，并且记录了所有这些细节或它们的不存在之处。ChatGPT几乎肯定见过许多其他编程语言规范、解析器规范以及数百万行常见编程语言的代码。如果一个人拥有这些背景知识和资源，进行必要的逻辑归纳来避免全部三个错误应该不会太困难。然而，LLM并不执行任何归纳过程，因此尽管信息广泛可用，它仍然会出错。这并不是说结果不令人印象深刻，它可以成为加速你自己代码开发或使用不熟悉的API和语言的宝贵工具。但这也提醒你，此类工具对广泛使用且有文档记录的语言和API效果更好，尤其是当它们符合预期标准时。例如，大多数数据库使用SQL语言，这使得对也使用SQL的新数据库的准确外推更有可能。

<details>
<summary>英文原文</summary>

This example also highlights the limits of perceived versus actualized “reasoning” within LLMs today. The complete language specification for Modula-3 is available online and has documented all of these details or their lack of existence. ChatGPT has almost surely seen many other coding language specifications, parser specifications, and millions of lines of code in common programming languages. If a person had this background knowledge and resources, performing the logical induction required to avoid all three errors should not be too challenging. However, the LLM does not perform any induction process and, thus, makes errors despite the breadth of available information.

This is not to say that the result is not massively impressive, and it can be a valu-able tool to accelerate your own code development or use of unfamiliar APIs and languages. But it also informs you that such tools will work far better for widely used and documented languages and APIs, especially if they conform to expected standards. For example, most databases use the language SQL, which makes accurate extrapolation of how to use a novel database that also uses SQL more likely.

</details>

### 4.3.1 无法识别正确任务

另一个值得注意的LLM失败案例是，当它们无法正确识别应当执行的任务时，它们会回答与用户意图不同的问题。未能正确识别任务曾经是原始GPT-3等模型面临的重大难题，但后续工作通过增加训练数据中结构化任务示例的数量，显著提升了后续ChatGPT模型遵循指令的能力。然而，ChatGPT在某些情况下仍然无法识别正确的任务。例如，通过询问某个与常见任务细微不同的罕见任务，或以不熟悉的方式修改一个模型见过多次的问题，就能可靠地诱发这种行为。

一个著名的例子是渡河谜题：农民要把卷心菜、山羊和狼用船运过河。谜题规定，山羊不能单独与卷心菜在一起（否则山羊会吃掉卷心菜），也不能单独与狼在一起（否则狼会吃掉山羊）。ChatGPT能快速解决这个谜题，但如果我们稍微改变谜题的逻辑结构，模型仍然沿用旧的推理方式，如图4.9所示。虽然LLM的错误通常难以追溯到具体原因，但在这个案例中，模型会欣然告诉我们“确保没有任何物品（卷心菜、山羊、狼）无人看管地留在一起”。虽然这个指令在原始版本的卷心菜/山羊/狼问题中是正确的（并且很可能是基于逻辑问题中的约束规范），但模型没有意识到，在给定版本中，山羊和狼单独在一起并无问题。不仅没有必要像建议的那样交换动物，而且ChatGPT的建议会失败，因为它将狼和卷心菜放在了一起，而这是明确禁止的。这个现象的另一个有趣例子是，当你消除了需要留在岸上的物品时（即所有物品都能一起带走），从任何逻辑理解来看，显然你只需把所有东西装上船然后过河即可。但模型再次因为过于习惯于回答它多次见过的那个版本的问题而这样做。

<details>
<summary>英文原文</summary>

Another notable case in which LLM’s fail is when they cannot correctly identify the task they are supposed to perform and instead will answer a question different from what the user intended. Failure to correctly identify the task used to be a substantial problem for models like the original GPT-3, but subsequent work aimed at increasing the number of task-structured examples in the training data has substantially increa-sed the ability of later ChatGPT models to follow instructions. However, ChatGPT will still fail to identify the correct task in some cases. For example, this behavior can be elicited reliably by asking about an unusual task subtly different from a common task or by modifying a problem it has seen many times in an unfamiliar way. One example is a famous logic puzzle about bringing a cabbage, a goat, and a wolf across a river in a boat. The puzzle stipulates that the goat can’t be left alone with the cabbage (as the goat will eat it) or with the wolf (which will devour the goat). ChatGPT can quickly solve this puzzle, but if we change the logical structure of the puzzle slightly, the model continues to use the old reasoning as shown in figure 4.9. While it is often hard to trace errors made by LLMs back to specific causes, in this case, the model happily tells us to “ensure that none of the items (cabbage, goat, wolf) are left together unsupervised.” While this instruction is correct in the original version of the cabbage/goat/wolf problem (and was likely based on the specification of the constraints in the logic problem), the model is unaware that the given version has no problem with the goat and wolf being alone together. Not only is there no need to swap the animals as suggested, but ChatGPT’s advice will fail because it places the wolf and cabbage together, which we explicitly disallowed. Another curious example of this phenomenon happens when you remove the need to leave anything behind. Any logical understanding of the puzzle makes it clear that you only need to load everything into the boat and cross. Yet again, the model is too accustomed to answering the version of the problem that it has seen many times before and does so.

</details>

![图 4.9 展示了ChatGPT无法解决经典逻辑谜题的两个修改版本，原因在于LLM的训练方式。频繁以相同通用形式出现的内容（例如著名的逻辑谜题）会导致模型机械重复常见答案。即使内容发生了对人类而言显而易见的重大修改，这种情况仍可能发生。](assets/fig-4-9-f9f8fb05c6.jpg)

*图4.9展示了ChatGPT无法解决经典逻辑谜题的两个修改版本，原因在于LLM的训练方式。频繁以相同通用形式出现的内容（例如著名的逻辑谜题）会导致模型机械重复常见答案。即使内容发生了对人类而言显而易见的重大修改，这种情况仍可能发生。*

要理解这一现象的原因，首先需要回顾第3章中讨论的LLM训练的自回归特性。模型被明确鼓励基于先前内容生成内容。为解决重构后的逻辑谜题而生成的内容，在单词和顺序上几乎与解决原始逻辑谜题的内容完全相同。因此，在Transformer层的查询-键配对中，存在一个良好的模糊匹配，从而产生构成原始谜题解法的数值。模糊匹配完成后，先前的解法便通过Transformer使用的注意力机制被忠实地返回。尽管这一策略对模型准确预测这一著名谜题的词元非常有效，但它并未涉及对谜题逻辑的推理。

<details>
<summary>英文原文</summary>

To understand why this happens, it is important to recall the autoregressive nature of LLM training discussed in chapter 3. The model is explicitly incentivized to generate content based on prior content. The content generated to solve the reframed logic puzzle appears almost exactly like the content that solves the original logic puzzle in terms of words and order. As a result, it is a good fuzzy match in the transformer layer’s query and key pairing that produces the values that make up the original puzzle’s solution. The fuzzy match is made, and the previous solution is faithfully returned via the attention mechanism used by the transformers. While this strategy is excellent for the model to correctly predict the tokens for the famous puzzle, it does not involve reasoning through the puzzle’s logic.

</details>

### 4.3.2 LLM无法规划

LLM自回归特性的另一个微妙限制是，它们只能处理上下文中的信息。LLM被训练为接受输入并生成合理的后续内容。然而，它们无法进行规划、做出承诺或跟踪内部状态。一个很好的例子是当你尝试与ChatGPT玩“20个问题”游戏时。人类玩20个问题时，会预先承诺一个隐藏信息，即他们选择用来通过答案识别的对象。当ChatGPT玩这个游戏时，它会逐个回答问题，然后事后找到一个与所给答案一致的输出。图4.10展示了这个例子，其中显示了玩20个问题的可能对话树。当有人与LLM玩游戏时，会随机选择其中一个对话树，而不是产生一个在整个游戏中保持一致的目标对象。

<details>
<summary>英文原文</summary>

Another subtle limitation of the autoregressive nature of LLMs is that they can only work with the information they see in context. LLMs are trained to take an input and produce a plausible continuation. However, they cannot plan, make commitments, or track internal states. A great example occurs when you attempt to play the game 20 questions with ChatGPT. When a human plays 20 questions, they precommit to a piece of hidden information, the object they’ve chosen to use the answers to identify. When ChatGPT plays this game, it answers questions individually and then, after the fact, finds an output consistent with the provided answers. This example is illustrated in figure 4.10, which shows possible dialog trees for playing 20 questions. When someone plays a game with an LLM, one of these dialog trees is chosen randomly instead of coming up with a target object that stays consistent throughout the game.

</details>

![图 4.10 对话代理在游戏开始时并不指定具体对象。](assets/fig-4-10-d5bd35a780.png)

*图4.10 对话代理在游戏开始时并不指定具体对象。*

### 4.4 如果大语言模型不擅长外推，还能用吗？

大部分需要完成的工作并不新颖。至少，还没有新颖到足以让LLM束手无策。然而，认识到LLM的能力在需要更多逻辑或细微差别时会迅速下降，有助于你缩小其使用范围。

在设计生产级计算机系统时，一个关键因素是明确工具的使用时机和方式范围。当你将像ChatGPT这样的LLM产品开放给没有特定范围的普通用户时，人们会让它做各种你意想不到的、随机的、疯狂的事情。虽然这对研究可能很好，但通常不适用于生产应用。尽管你的用户和客户会尝试用你的LLM应用做不可预测的事情，但假如你限制系统的访问权限，并围绕用户拥有特定目标、有限用例来设计，甚至限制他们的输入如何到达你的LLM。在这种情况下，你可以构建用户体验更可靠的产品。

<details>
<summary>英文原文</summary>

Most work that needs to be done is not novel or new. At least, it’s not novel or new enough to a degree that would make an LLM fail. However, understanding that an LLM’s abilities degrade quickly as more logic or nuance is required can help you narrow the scope of how you use it.

When we design production-grade computer systems, an essential factor to con-sider is the scope of when and how the tool will be used. When you make an LLM product like ChatGPT available to a general audience without a specific scope, people will ask it to do all sorts of random, crazy things you do not expect. While this might be great for research, it is often not practical for production applications. Although your users and customers will try to do unpredictable things with your LLM application, suppose you limit who has access to the system and design around your users having a specific goal, limited use cases, or even restrict how their inputs get to your LLM. In that case, you can build something with a much more reliable user experience.

</details>

如何在没有用户输入的情况下使用大语言模型？

<details>
<summary>英文原文</summary>

How can I use an LLM without user input?

</details>

大语言模型非常适合提供低门槛的编码或数据处理，尤其是在处理格式不甚规整或未经整理的数据时进行日常任务。不过，通过给用户提供有限的选择集，可以在降低风险的同时获得实用性。将提示限定为少量代码选项供用户选择，或者让用户决定在哪个数据源（例如某个内部数据库）上运行提示，这样就能阻止（大部分）用户向大语言模型输入任意文本。

<details>
<summary>英文原文</summary>

LLMs are excellent at providing low-effort coding or data processing, especially when you are doing everyday tasks on data that is not so cleanly formatted or curated. However, you can get utility without as much risk by giving users a finite set of choices. Having a limited set of prompts as code that a user can choose from or letting a user decide what data source (e.g., some internal database) a prompt is run over allows you to keep (most) people from giving an LLM arbitrary text.

</details>

相反，你可能会问：“我们能否检测出新颖的请求，并给用户返回一个错误？”理论上，是的，你可以尝试这样做。首先，我们不鼓励这样做，因为从用户体验角度来看，这并不理想。其次，这变成了一个称为新颖性检测或异常检测的任务。这个问题具有挑战性，并且很可能无法以绝对无错的方式解决。因此，我们鼓励预防优于检测，选择那些不需要通过分析LLM输入或输出就能高度准确预测失败的使用场景。

<details>
<summary>英文原文</summary>

Instead, you may ask, “Can we detect novel requests and give the user some error instead?” Hypothetically, yes, you could try to do this. First, we discourage it because it is not great from a user experience perspective. Second, it becomes a task known as novelty detection or outlier detection. This problem is challenging and is likely impossible to solve in a way that is guaranteed to be error-free. As a result, we encourage prevention over detection by choosing use cases that do not require highly accurate prediction of failures through the analysis of LLM input or output.

</details>

提示的应用——提示是一门艺术，旨在为大型语言模型构建输入，以引导其产生理想的行为。语言模型对输入的措辞方式非常敏感，因此设计出能引发恰当回应的输入能力显得尤为珍贵。在使用大语言模型时，一个反复出现的问题是人们通常不思考如何正确地与它们交互。提示大语言模型的最佳方式是：设想你期望的输出类型在训练数据中会是什么样子，然后写下它的开头四分之一内容。然而，人们往往直接描述想要语言模型完成的任务，认为这样的说明能让模型专注于问题。不幸的是，这种方法结果不稳定，并催生了通过向模型输入大量指令与响应对作为训练数据来微调大语言模型的研究。

<details>
<summary>英文原文</summary>

Applications for prompting Prompting is the art of crafting an input to a large language model that induces desirable behavior. Language models can be very sensitive to the exact framing of their inputs, making the ability to design inputs that are responded to appropriately highly valuable. A recurring theme in using LLMs is that people typically don’t think about how to interact with them correctly. The best way to prompt an LLM is to think about how the kind of output you’re interested in would look like in the training data and then write the first quarter of it. Instead, people often describe the task they want a language model to perform, assuming that this clarification will keep an LLM focused on the problem. Unfortunately, the approach yields inconsistent results and has inspired research in tuning LLMs by feeding them a large number of instructions and responses as training data.

</details>

### 4.5 越大越好？

2019年，Rich Sutton提出了“苦涩的教训”这一术语，用以描述他在机器学习领域的经验。“从70年的人工智能研究中可以学到的最重要一课是，那些利用计算能力的通用方法最终是最有效的，而且优势巨大”[7]。

人们真切地感受到，Transformer是这一原则的终极体现。你可以不断将它们做得更大，用更高的并行度训练，并增加更多GPU。这与RNN形成鲜明对比，后者无法像Transformer那样高效并行化。我们在图像领域也看到了这一点，生成对抗网络（GAN）方法难以达到十亿参数规模。LLM中基于Transformer的方法可以轻松扩展到数百亿参数，从而构建更大、更好的模型。

从解决方案设计的角度来看，你今天的原型可能会因模型大小而遇到显著的限制。更大的模型需要更多资源，预测时间也更长。你的用户能接受的最大响应时间是多少？以这个速度运行你的模型所需的硬件有多昂贵？模型大小的增长速度超过了消费级硬件的增长速度。因此，你可能无法将模型部署到嵌入式设备上，或者需要网络连接来分摊成本。因此，你需要在设计中考虑网络基础设施，以应对持续连接的需求。这一要求会增加电池消耗，由于需要持续开启Wi-Fi而非本地计算，这一点需要纳入考量。因此，尽管更大的模型更准确，但设计约束可能会妨碍它们以实用的方式部署。将这些约束与本章学到的关于LLM如何进行预测以及LLM在何时何地失效的用例结合起来，将让你具备充分的能力，去理解如何最有效地利用LLM解决你最关心的问题。

<details>
<summary>英文原文</summary>

In 2019, Rich Sutton coined the term “the bitter lesson” to describe his experience with machine learning. “The biggest lesson that can be read from 70 years of AI research is that general methods that leverage computation are ultimately the most effective, and by a large margin” [7].

There is a genuine sense that transformers are the ultimate example of this principle. You can keep making them bigger, training them with more parallelism, and adding more GPUs. This differs notably from RNNs, which cannot be parallelized nearly as efficiently as a transformer. We also see this in the image domain with Generative Adversarial Network (GAN) methods, which struggle to reach the billion-parameters scale. The transformer-based methods used in LLMs easily scale to the tens of billions, allowing the construction of bigger and better models. From a solutions design perspective, your prototype today may encounter signifi-cant constraints due to model size. Larger models require more resources and take longer to make predictions. What is the maximum response time your users will accept? How expensive is the hardware needed to run your model at this speed? The growth rate in model size exceeds the growth rate of consumer hardware. As a result, you may not be able to deploy your model to embedded devices, or you may require internet connectivity to offload the costs. Consequently, you need to consider networking infrastructure in your design to handle the need for continuous connection. This requirement increases battery usage, which is a consideration when continually running a Wi-Fi radio instead of local computing. So although larger JL 545 2230 models are more accurate, design constraints may prevent their deployment in a practical manner. Combining these constraints with the facts about how LLMs make their predictions and the use cases of when and where LLMs fail that you learned in this chapter positions you well for understanding how to use LLMs to solve the problems you care about most effectively.

</details>

### 总结

深度学习需要一个损失/奖励函数，专门量化算法在预测中的表现有多差。这个损失/奖励函数应该设计得与我们期望算法在现实中实现的总体目标相关联。梯度下降通过逐步使用损失/奖励函数来改变网络的参数。

大型语言模型通过预测下一个词元来训练以模仿人类文本。这个任务足够具体，可以训练模型执行它，但它与推理等高层次目标并非完美相关。大型语言模型在与训练数据中常见且重复的任务类似的任务上表现最佳，但在任务足够新颖时则会失败。

<details>
<summary>英文原文</summary>

Deep learning needs a loss/reward function that specifically quantifies how badly an algorithm is at making predictions This loss/reward function should be designed to correlate with the overarching goal of what we want the algorithm to achieve in real life. Gradient descent involves incrementally using a loss/reward function to alter the network’s parameters.

LLMs are trained to mimic human text by predicting the next token. This task is sufficiently specific to train a model to perform it, but it does not perfectly correlate with high-level objectives like reasoning. LLMs will perform best on tasks similar to common and repetitive tasks observed in its training data but will fail when the task is sufficiently novel.

</details>



---

<a id="ch5"></a>

## 第 5 章: 如何约束 LLM 的行为

如何约束LLM的行为？

### 本章涵盖

- 约束LLM行为以提升其实用性

- 约束 LLM 行为的四个领域

- 微调如何让我们更新LLM

- 强化学习如何改变LLM的输出

- 使用检索增强生成修改 LLM 的输入

<details>
<summary>英文原文</summary>

- Constraining LLM behavior to make them more useful
- The four areas where we can constrain LLM behavior
- How fine-tuning allows us to update LLMs
- How reinforcement learning can change the output of LLMs
- Modifying the inputs of an LLM using retrieval augmented generation

</details>

通过控制模型允许产生的输出来让模型更有用，这看似反直觉，但在使用大语言模型时几乎总是必要的。之所以需要这种控制，是因为当给定任意文本提示时，大语言模型会试图生成它认为合适的响应，而不考虑其预期用途。考虑一个帮助顾客买车的聊天机器人：你不会希望大语言模型偏离脚本，仅仅因为顾客问了一些与带孩子去踢足球相关的事情，就开始和他们谈论田径或体育。

本章将更详细地讨论为什么要限制或约束大语言模型的输出，以及此类约束相关的细微差别。准确约束大语言模型是最难完成的任务之一，这是因为大语言模型根据训练数据中的观察来补全输入的本质。目前还没有完美的解决方案。我们将讨论可以修改大语言模型行为的四个潜在环节：

<details>
<summary>英文原文</summary>

It may seem counterintuitive that you can make a model more useful by controlling the output the model is allowed to produce, but it is almost always necessary when working with LLMs. This control is necessitated by the fact that when presented with an arbitrary text prompt, an LLM will attempt to generate what it believes to be an appropriate response, regardless of its intended use. Consider a chatbot helping a customer buy a car; you do not want the LLM going off-script and talking to them about athletics or sports just because they asked something related to taking the vehicle to their kid’s soccer games.

In this chapter, we will discuss in more detail why you would want to limit, or constrain, the output an LLM produces and the nuances associated with such constraints. Accurately constraining an LLM is one of the hardest things to accomplish because of the nature of how LLMs are trained to complete input based on what they observe in training data. Currently, there are no perfect solutions. We will discuss the four potential places where an LLM’s behavior can be modified:

</details>

在训练前，通过筛选用于训练LLM的数据、改变LLM的训练方式、在特定数据集上微调LLM、以及在训练完成后编写特殊代码来控制模型输出——这四种情形总结在图5.1中。LLM开发的每个阶段都滋养着下一个阶段。微调阶段是在较小数据集上进行的第二轮训练，对于理解ChatGPT等工具当今的运作方式最为重要，也是你实际中最可能采用的方法。我们已在第2至4章学习过，第一轮规模更大的训练阶段通常称为预训练，因为它发生在微调使模型变得有用之前。预训练过程产生的模型有时被称为基础模型或基座模型，因为它是构建特定任务（即微调）模型的起点。

<details>
<summary>英文原文</summary>

Before training occurs, curating the data used to train the LLM By altering how the LLM is trained By fine-tuning the LLM on a set of data By writing special code after training is complete to control the outputs of the model These four cases are summarized in figure 5.1. Each stage of developing an LLM feeds into the next. The fine-tuning stage, a second round of training done on a smaller data set, is the most important for how tools like ChatGPT function today and the most likely approach you might use in practice. The first, larger training stage we’ve learned about in chapters 2 to 4 is often referred to as pretraining because it occurs before fine-tuning makes the model useful. The model produced by the pretraining process is sometimes referred to as either a base model or a foundation model because it is a point from which to build a task-specific, or fine-tuned, model.

</details>

![图 5.1 可以在四个地方干预LLM以改变或约束其行为。图中部展示了模型训练的两个阶段，其中模型参数被修改。左侧，可以在模型训练前修改训练数据。右侧，可以在模型训练后拦截模型输出并编写代码处理特定情况。](assets/fig-5-1-89784e7b2d.png)

*图5.1 可以在四个地方干预LLM以改变或约束其行为。图中部展示了模型训练的两个阶段，其中模型参数被修改。左侧，可以在模型训练前修改训练数据。右侧，可以在模型训练后拦截模型输出并编写代码处理特定情况。*

由于微调的重要性和有效性，本章将重点探讨这一因素及其执行方式。

<details>
<summary>英文原文</summary>

Due to the importance and effectiveness of fine-tuning, we will spend most of the chapter on that factor and how it may be performed.

</details>

### 5.1 我们为何要约束行为？

大语言模型之所以取得巨大成功，是因为它们首次实现了“用自然语言告诉计算机做什么，它就会照做”这一理念。通过非常明确地表述你的需求，设定具体的细节层次并指定特定的语气，你可以让大语言模型成为一个极其有效的工具。

<details>
<summary>英文原文</summary>

LLMs are incredibly successful because they are the first technology to deliver on the idea of “Tell a computer what to do in plain English, and it does it.” By being very explicit about what you want to happen, establishing a specific level of detail and specifying a certain tone, you can get an LLM to be a shockingly effective tool.

</details>

这组详细的指令被称为提示词，而设计优质提示词的技艺则被称为提示工程。例如，我们可以为汽车销售机器人开发一个提示词，如图5.2所示。

<details>
<summary>英文原文</summary>

This detailed set of instructions is called a prompt, and the art of designing a good prompt has been referred to as prompt engineering. For example, we could develop a prompt for a car-selling bot as demonstrated in figure 5.2.

</details>

![图 5.2 类似 ChatGPT 这样的商业 LLM 被设计成能够遵循指令（在一定限度内），并且在执行大量低认知或模式匹配任务时效率非常高。这些任务包括风格化写作（如模式匹配）和指令遵循（如扮演汽车销售员）。](assets/fig-5-2-e59bee1407.jpg)

*图 5.2 类似 ChatGPT 这样的商业 LLM 被设计成能够遵循指令（在一定限度内），并且在执行大量低认知或模式匹配任务时效率非常高。这些任务包括风格化写作（如模式匹配）和指令遵循（如扮演汽车销售员）。*

你可以给LLM一个提示，要求将数据组织成逗号分隔值，以便复制到Excel中。你也可以设计一个提示，说明如何将自由格式的调查回复分类成总结的主题。在所有情况下，提示都是将行为限制或约束到特定任务和目标集的一种练习。然而，前几章讨论的分词和训练技术并不能支持这种指令遵循。

<details>
<summary>英文原文</summary>

You could give an LLM a prompt on organizing data into comma-separated values so that you can copy them into Excel. You could design a prompt about how to categorize free-form survey responses into summarized themes. In all cases, prompting is an exercise in limiting, or constraining, the behavior to a particular task and set of goals. Yet, the tokenization and training techniques we have discussed in the previous chapters do not enable this kind of instruction following.

</details>

### 5.1.1 基础模型并不易用

按照第4章所述流程训练LLM，得到的模型通常称为基础模型，因为它可以作为构建应用或微调模型的基础平台。

不幸的是，基础模型对大多数人来说并不十分有用，因为它们没有通过用户友好的界面暴露其底层知识，难以保持对话主题，有时还会产生不良内容。

基础模型甚至在训练时都没有被赋予像ChatGPT那样的聊天机器人概念。

<details>
<summary>英文原文</summary>

Training an LLM following the process described in chapter 4 produces a model typically referred to as a base model because it can serve as a base platform for building applications or fine-tuned models. Unfortunately, base models are not very useful to most people because they don’t expose their underlying knowledge via a user-friendly UI, they can be challenging to keep on-topic, and sometimes they produce unsavory content. Base models are not even trained with the concept of being a chatbot like ChatGPT is.

</details>

### 并非所有模型输出都是理想的

- 有时候，模型认为文档中接下来可能出现的内容并非我们所期望的。导致这种情况的原因有多种，其中包括记忆——有时，LLM会生成长度很长、与训练数据中序列完全一致的副本，这通常被称为记忆化，意指模型从训练集中通过记忆再现文本。记忆化可能是有益的，例如记住特定事实性问题的答案。例如，如果有人问“亚伯拉罕·林肯出生于何时？”，我们希望模型能准确复述出“1809年2月12日”。然而，如果它导致模型侵犯版权，那可能就非常有害了。如果有人要求获取“爱德华·拉夫所著《深入深度学习》的副本”，而模型生成了逐字复制的版本，那么爱德华可能会因版权侵权而对你感到不满！

- 网络上的不良内容——互联网上并非所有内容都适合展示给用户。互联网上充斥着大量粗鄙和仇恨的内容，以及从常见误解到阴谋论等各种事实错误的信息。尽管模型开发者通常会在训练前尝试过滤掉这些数据，但这并非总是可行的。

- 信息缺失与新知——不方便的是，在我们训练模型之后，世界不断演变，变得更加复杂。因此，一个基于截至2018年信息训练的模型将不知道之后发生的任何事情，例如新冠肺炎疫情或犹如噩梦般的“僵尸机器人”发明[1]。但你可能希望模型了解这些进展以保持其有用性，而无需花费高昂成本从头重新训练基础模型。

<details>
<summary>英文原文</summary>

Sometimes, what a model thinks is likely to come next in a document is undesirable. There are several reasons for this, including Memorization—Sometimes, LLMs can generate long, exact copies of sequences found in their training data, which is often referred to as memorization, which refers to the idea that the text is being reproduced by memory from the training set. Memorization can be beneficial, such as memorizing the answers to specific factual questions. For example, if someone asks, “When was Abraham Lincoln born?” you want the model to regurgitate “February 12, 1809.” However, it can also be substantially detrimental if it leads a model to infringe copyright. If someone asks for “A copy of Inside Deep Learning by Edward Raff,” and the model produces a verbatim copy, Edward may be upset with you for copyright infringement!

Bad things on the web—Not everything found on the internet is something you would want to expose a user to. There is a lot of vile and hateful content on the internet, as well as factually incorrect info ranging from common misconcep-tions to conspiracy theories. While model developers often try to filter out this data before training the model, that’s not always possible. Missing and new information—Inconveniently, the world keeps evolving and growing more complex after we train our models. So a model trained on information up to 2018 will not know of anything that happened after, such as COVID-19 or the nightmare-fuel invention of necrobotics [1]. But you may want your model to know about these developments to remain useful, without having to pay a considerable cost to retrain your base model from scratch.

</details>

等待法律体系跟上步伐。我们不是你的律师，这也不是法律教材！围绕LLM的法律问题十分复杂，在合理使用与侵权方面存在诸多细微差别。搜索引擎可以直接显示来源内容，但为什么可以这样做？一系列明确针对这些问题的法律，如《数字千年版权法案》（DMCA），以及法院判例确立的先例，如Field诉Google案（412 F.Supp. 2d 1106 [D. Nev. 2006]），逐步确立了可接受与不可接受的使用界限。然而，立法和司法判例的建立需要时间，而生成式AI的革命无法完全纳入现有的法律框架。

<details>
<summary>英文原文</summary>

Waiting for the legal system to catch up We are not your lawyers; this is not a law book! The legal problems around LLMs are complex, and there is a lot of nuance regarding fair use and infringement. Search engines can show you the content of their sources verbatim, but why? A combina-tion of laws explicitly addressing these concerns, such as the Digital Millennium Copyright Act (DMCA), and precedents set by court rulings, such as Field v Google, Inc. (412 F.Supp. 2d 1106 [D. Nev. 2006]), establish acceptable and nonaccepta-ble use over time. However, legislation and court cases take time to create, and the revolution of generative AI does not fit neatly into existing legal understanding.

</details>

GPT-3.5和GPT-4经过改进，会回避回答不知道的问题（尽管并非总能成功）。但我们可以看看一些开源基础模型，比如GPT-Neo，在没有主动对策的情况下会出现什么情况。例如，如果我们编造一种新药MELTON-24，然后问“MELTON-24是什么？它能帮助我改善睡眠吗？”我们会得到这样的无用回复：“褪黑素会伴随大量睡眠问题，包括失眠和疲劳。这会导致失眠，以及为什么避免食用抑制褪黑素的食物很重要。”在这种情况下，MELTON与褪黑素的相似性以及“睡眠”的提示足以让模型抓住褪黑素这个主题。

但显然，这个回答是荒谬的，因为MELTON-24并不存在。理想情况下，我们希望模型能够识别并做出回应，承认自己缺乏信息，而不是像这样输出更多文本。

<details>
<summary>英文原文</summary>

GPT-3.5 and 4 have been improved to avoid answering things they do not know (not always successfully), but we can look to some open-source base models like GPT-Neo to see what happens without proactive countermeasures. For example, if we make up the new fake drug, MELTON-24, and ask “What is MELTON-24, and can it help me sleep better?” we get the unhelpful response: “There is a great number of sleep problems that go with Melatonin, including insomnia and fatigue. This causes insomnia, and why it is important to avoid certain foods that can suppress melatonin.”

In this case, the similarity of MELTON to melatonin and the prompt of “sleep” were enough for the model to catch onto the melatonin theme. Still, the answer is obviously nonsensical since MELTON-24 does not exist. Ideally, we want the model to recognize and respond, acknowledging its lack of information rather than producing more text like it has done here.

</details>

### 5.1.3 某些情况需要特定格式

如果用户要求以特定格式提供数据，例如像JSON这样的结构化文本格式（关于计算机间数据交换的常见格式示例，请参见https://en.wikipedia.org/wiki/JSON），而你没有正确匹配每个左括号或右括号，或没有正确编码特殊字符，那么输出将无法满足用户的需求。

无论输出多么复杂或接近正确，格式要求几乎总是严格的要求。

我们在第4章中提出了这类问题的一个例子：当我们要求ChatGPT用Modula-3编写代码时，它借用了对Modula-3无效的Python语法。

如果代码违反语法规则，它将无法编译。

LLM采用概率方法为特定期望输出生成文本，不能保证100%遵守所有期望的语法规则。

<details>
<summary>英文原文</summary>

If a user asks for data in a specific format, such as a structured text format like JSON (for an example of a common format for exchanging data between computers, see https://en.wikipedia.org/wiki/JSON), and you do not match every opening or closing bracket or encode special characters properly, the output won’t satisfy their goals. It does not matter how sophisticated or close to correct the output may have been; formatting requirements are almost always strict requirements. We presented an example of this kind of problem in chapter 4 when we asked ChatGPT to write code in Modula-3, and it borrowed Python syntax that was invalid for Modula-3. The code won’t compile if it violates syntax rules. An LLM’s probabilistic approach to generating text for specific desired outputs will not guarantee that all desired syntax rules are adhered to 100% of the time.

</details>

### 5.2 微调：改变行为的主要方法

既然我们已经了解了为何需要约束和控制LLM行为的种种原因，现在就可以更好地向模型引入新信息，以解决我们试图解决的问题，同时避免产生有害或可能违法内容的问题。请记住，虽然有四个不同的干预点可以改变行为，但微调远比其他方法有效。无论是像OpenAI [2]这样的闭源选项，还是像Hugging Face [3]这样的开源工具，以及其他众多选择，都提供了多种微调方案，使其成为从业人员最易上手的方法。

任何微调方法都会产生相同的效果——生成一个具有更新参数、从而控制其行为的新LLM变体。因此，我们可以混合搭配不同的微调策略，因为它们产生的根本效果是一样的：一组新的参数，可以直接使用，也可以再次调整。一个人的基础模型可能是另一个人的微调模型。这在许多开源LLM中很常见：一个初始模型（例如Llama）会被另一方修改（例如，你可以找到许多“Instruct Llama”模型），然后你可以根据自己的数据或特定用例进一步微调。

自定义LLM最直接的方式是通过提示工程，不断迭代优化提示词，直到获得期望的行为。但如果这种方法效果不佳，微调就是下一个合理的步骤。这一步涉及一定程度的额外工作和成本增加，例如收集微调所需的数据以及获取运行微调会话的硬件。

你尤其需要了解两种微调方法：监督式微调（SFT）和听起来更吓人的“基于人类反馈的强化学习”（RLHF）。SFT方法更直接，非常适合将新知识融入模型，或者简单地在你的偏好应用领域中提升模型表现。RLHF则更复杂，但它提供了一种让LLM遵循更困难、更抽象目标（如“做个好聊天机器人”）的策略。

<details>
<summary>英文原文</summary>

Now that we understand various reasons why we want to constrain and control the behavior of an LLM, we are better prepared to introduce new information to the model to address the problem we are trying to solve while avoiding the problem of producing harmful or legally questionable content. Remember, while there are four different places where we can intervene to change behavior, fine-tuning is far more effective than the others. Both closed source options like OpenAI [2] and open source tools like Hugging Face [3], among many others, have varying options for fine-tuning, making it the most accessible method for practitioners. Any fine-tuning method will have the same effect—producing a new variant of an LLM with updated parameters that control its behavior. As a result, it is possible to mix and match different fine-tuning strategies because the fundamental effect they produce is the same: a new set of parameters that can be used as is or altered yet again. One person’s base model could be another person’s fine-tuned model. This happens with many open source LLMs where an initial model (e.g., Llama) will be altered by another party (e.g., you can find many “Instruct Llama” models), which you may then further fine-tune to your data or specific use case. The most straightforward way to customize an LLM is by prompting and iteratively refining prompts until the desired behavior is obtained. However, fine-tuning is the next logical step if that does not work well. This step involves a moderate increase in effort and cost, such as collecting the data to fine-tune and acquiring the hardware for running a fine-tuning session.

Two fine-tuning methods you should know in particular are supervised fine-tuning (SFT) and the more intimidatingly named reinforcement learning from human feedback (RLHF). SFT is the more straightforward approach and is excellent for incorporating new knowledge into a model or simply giving it a boost in your preferred application domain. RLHF is more complex but provides a strategy for getting an LLM to follow harder and more abstract goals like “be a good chatbot.”

</details>

### 5.2.1 监督微调

影响模型输出的最常见方法是SFT。SFT使用高质量、通常由人类编写的示例内容，这些内容包含对任务至关重要的信息，但基模型不一定能很好地体现这些信息。

这通常是因为LLM在大量通用内容上训练，而通用内容与您的具体需求重叠甚少。

如果您经营医院，LLM很少见过医生的笔记；如果您经营律师事务所，LLM可能没看过太多证词笔录；如果您经营维修店，LLM可能没见过您手头的所有手册。

<details>
<summary>英文原文</summary>

The most common way to influence a model’s output is SFT. SFT involves taking high-quality, typically human-authored, example content that captures information vital to your task but is not necessarily well reflected in the base model. This often occurs because LLMs are trained on a large amount of generally available content, which may have minimal overlap with your specific needs. If you run a hospital, LLMs have seen very few doctors’ notes. If you run a law firm, an LLM probably has not seen too many deposition transcripts. If you run a repair shop, LLMs probably have not seen all the manuals you might have access to.

</details>

警告：微调是为模型添加新信息的有用方法，但也可能带来安全风险。

如果你想在医疗记录上构建LLM，那么在示例医疗记录上对LLM进行微调是合理的。但现在存在风险：有人可能让你的LLM再现微调数据中包含的敏感信息，因为从根本上说，LLM会基于其见过的训练数据来完成输入。底线：不要在你希望保密的数据上训练或微调LLM。

<details>
<summary>英文原文</summary>

WARNING Fine-tuning is a helpful way to add new information to your model but can also have security ramifications. If you want to build an LLM on medical records, it makes sense to fine-tune the LLM on example medical records. But now there is a risk someone could get your LLM to reproduce sensitive information contained in that fine-tuning data because fundamentally, LLMs attempt to complete input based on the training data they have seen. The bottom line: do not train or fine-tune LLMs on data you want to keep private.

</details>

再来看看我们汽车公司及其销售聊天机器人的例子。来自第三方的基础模型通常对汽车有基本了解，但很可能并不了解该公司产品的全貌。通过在内部手册、聊天记录、电子邮件、营销资料及其他内部文档上对模型进行微调，你可以确保模型尽可能多地掌握关于你的汽车的信息。你甚至可以编写关于你的车辆相对于竞争对手的优势、优点、脚本等示例文档，以确保 LLM 拥有你想要它掌握的信息。

<details>
<summary>英文原文</summary>

Consider again our example of the car company and its sales chatbot. A base model from a third-party source may generally be aware of cars but probably will not know everything about the company’s products. By fine-tuning a model on internal manuals, chat histories, emails, marketing materials, and other internal documents, you could ensure the model is prepared with as much information as possible about your cars. You could even write example documents about the merits of your vehicles over competitors, advantages, scripts, and more to ensure that the LLM is armed with the information you want it to have.

</details>

SFT的机制很容易解释。正如我们之前提到的，SFT只需要更多的文档。这些文档可以是任何能够提取文本的格式。这构成了应用SFT所需的全部工作，因为SFT只是重复你在第4章学到的训练过程。图5.3显示SFT的过程与你之前看到的相同。不同之处在于，第一次训练基础模型时，初始参数是随机的且没有用处。第二次微调时，你从基础模型的参数开始，这些参数编码了基础模型通过观察训练数据所学到的内容。

<details>
<summary>英文原文</summary>

The mechanics of SFT are easy to explain. As we’ve alluded to, SFT simply needs more documents. They can be in any format from which text can be extracted. This constitutes all of the work necessary to apply SFT because SFT is just repeating the same training process you learned in chapter 4. Figure 5.3 shows that the process for SFT is the same as you saw previously. The difference is that the initial parameters are random and unhelpful the first time you train the base model. The second time you fine-tune, you start with the base model’s parameters that encode what the base model has learned by observing its training data.

</details>

![图 5.3 监督微调（SFT）是一种提升模型表现的简单方法。重复构建基础模型所用的相同流程：在基础模型基于大量通用数据完成训练后，你继续在小规模专用数据集合上训练。](assets/fig-5-3-001f3716b9.png)

*图5.3 监督微调（SFT）是一种提升模型表现的简单方法。重复构建基础模型所用的相同流程：在基础模型基于大量通用数据完成训练后，你继续在小规模专用数据集合上训练。*

令人高兴的是，你现在已经对SFT有了很好的理解。与原始训练过程一样，它重用了“预测下一个词”这一任务，以确保模型内部融入了新文档的信息。作为预测下一个词的直接后果，SFT也无法改变LLM的激励。因此，像“不要对用户说脏话”这样的抽象目标，通过SFT是很难实现的。

<details>
<summary>英文原文</summary>

Delightfully, you now have a good understanding of SFT. Like the original training process, it reuses the “predict the next token” task to ensure your model has infor-mation from the new documents built inside. As a direct consequence of predicting the next token, SFT also does not allow us to change the incentives of the LLM. For this reason, abstract goals like “Do not curse at the user” are difficult to achieve with SFT.

</details>

微调陷阱 通过重用第4章的梯度下降策略，所有微调方法都倾向于继承两个围绕LLM返回其训练内容的能力的问题。由于SFT非常简单，现在是时候回顾除SFT之外更广泛的微调问题了。

无法保证SFT能正确保留你提供的信息。这个问题，被称为灾难性遗忘[4]，发生在当你训练新数据但不继续训练旧数据时，模型开始——

<details>
<summary>英文原文</summary>

FINE-TUNING PITFALLS By reusing the gradient descent strategy from chapter 4, all fine-tuning methods tend to inherit two problems around an LLM’s ability to return content on which it was trained. Since SFT is so simple, this is a good time for us to review the broader problems with fine-tuning beyond just SFT.

There are no guarantees that SFT will retain the information you provide correctly. This problem, known as catastrophic forgetting [4], occurs when you train the model on new data but do not continue training on older data, and the model begins to

</details>

“遗忘”那些较早的信息。很难确定哪些信息会被遗忘，哪些不会。灾难性遗忘自1989年以来就是一个公认的问题[5]。换句话说，微调并非纯粹的信息叠加；你为此要牺牲一些东西。

<details>
<summary>英文原文</summary>

“forget” that older information. It is not easy to determine what will and will not be forgotten. Catastrophic forgetting has been a recognized problem since 1989 [5]. In other words, fine-tuning is not purely additive; you give up something for it.

</details>

### 5.2.2 基于人类反馈的强化学习

目前，RLHF是约束模型的主流范式。顾名思义，它借鉴了强化学习（RL）领域的方法。RL是一大类技术，算法必须做出多个决策以最大化长期目标，如图5.4所示，其中四个术语具有技术含义：

<details>
<summary>英文原文</summary>

At the time of writing, RLHF is the dominant paradigm for constraining models. As the name implies, it uses an approach from the field of reinforcement learning (RL). RL is a broad family of techniques where an algorithm must make multiple decisions toward maximizing a long-term goal, as shown in figure 5.4, where four terms are used with a technical meaning:

</details>

- 智能体——拥有某个总体目标并可能通过多个动作来实现该目标的实体/AI/机器人。

- 动作——智能体为推进其目标而可能执行或参与的所有可能行为的空间。

- 环境——受动作影响的地方/对象/空间。环境可能会因为该动作、其他智能体的动作或环境的自然持续变化而改变或不改变。

- 奖励——对改进（可能为负）的数值量化，该改进可能在任意次动作后发生或不发生。

<details>
<summary>英文原文</summary>

Agent—The entity/AI/robot with some overarching goal that it wishes to accomplish that may take multiple actions to achieve. Action—The space of all possible things the agent may be able to perform or engage in to advance the agent’s goals.

Environment—The place/object/space affected by an action. The environment may or may not change as a result of the action, actions taken by other agents, or the natural continuous change of the environment. Reward—The numeric quantification of improvement (which may be negative) that may or may not occur after any given number of actions.

</details>

![图 5.4 RL涉及迭代交互，其中你的动作的奖励可能需要很长时间才能显现，并且需要多个步骤才能实现。对于像ChatGPT这样的聊天机器人，环境是与用户的对话，动作是ChatGPT可能完成的无限可能的文本。在某种意义上，奖励变成了用户对话结束时对聊天机器](assets/fig-5-4-5c2e0644f2.png)

*图5.4 RL涉及迭代交互，其中你的动作的奖励可能需要很长时间才能显现，并且需要多个步骤才能实现。对于像ChatGPT这样的聊天机器人，环境是与用户的对话，动作是ChatGPT可能完成的无限可能的文本。在某种意义上，奖励变成了用户对话结束时对聊天机器人的满意度。*

在一个大语言模型作为聊天机器人与人类交互的例子中，用户就是环境。大语言模型本身是智能体，它能生成的文本就是动作。还剩最后一项需要指定：奖励。如果我们让用户对与聊天机器人的良好对话（例如，没有脏话、没有撒谎、提供有帮助的回复）打+1分，对糟糕的对话（例如，它建议毁灭全人类）打-1分，那么我们就将人类反馈添加到了强化学习中。

敏锐的读者可能会注意到，奖励听起来与第4章讨论的损失函数非常相似。事实上，我们那个好与坏对话的例子恰恰属于非常主观且难以量化的范畴，我们曾指出这是损失函数的反面教材。+1/-1 奖励并不平滑，因为值指向一个方向或另一个方向，没有中间地带，这是损失函数的另一个糟糕特性。

强化学习的强大之处之一在于它能处理非连续且难以量化的目标。我们使用“奖励”而非“损失”这个术语，正是为了体现这两种情况的区别。通常，强化学习能够学习的目标类型被称为不可微的。因此，这些目标无法使用诸如梯度下降等数学技术来学习，我们曾在第4章描述神经网络如何学习时介绍过这些技术。我们稍后将具体解释RLHF的工作原理。强化学习的一个警示是它计算开销大且需要大量数据。强化学习是出了名的难学。它通常比其他微调技术（如SFT）效果更差，因为强化学习需要比其它方法多得多的“正确”与“错误”示例，而且由于我们使用人类反馈来指导RLHF，结果并非总是完美。例如，在图5.5中，RLHF无法帮助大语言模型理解在RLHF训练期间未明确见过的基本指令，因为它不会为基础模型增加任何执行基本逻辑的能力，例如理解用户要求避免显示关于海豚的信息。

大语言模型并不像我们人类所理解的那样进行推理。通过收集数亿个“一切”的示例，你可以取得很大进展，但世界是奇特的。我们几乎没有证据表明大语言模型能够在出现新情况时可靠地生成令人满意的回复。然而，RLHF是目前约束大语言模型行为的最佳方法。尽管存在挑战，强化学习提供了一种基于梯度且需要可微目标的方法所不具备的学习方式。最重要的是，ChatGPT已经表明强化学习在许多情况下是有效的。那么，让我们深入探讨RLHF的工作原理。

<details>
<summary>英文原文</summary>

In the example of an LLM being used as a chatbot to interact with people, the users are the environment. The LLM is itself the agent, and the text it can produce is the action. This leaves one final thing to specify: the reward. If we were to get a user to score a +1 for a good conversation with a chatbot (e.g., no foul language, no lying, provided helpful responses) and a -1 for a lousy conversation (e.g., it suggested destroying all humans), then we would be adding human feedback to our reinforcement learning.

An astute reader might notice that a reward sounds suspiciously similar to the loss function discussed in chapter 4. In fact, our example of a good and bad conversation falls into the very subjective and difficult-to-quantify regime that we stated was a bad example of a loss function. The +1/-1 reward is not smooth because the value points in one direction or the other, and there is no middle ground, another poor characteristic for a loss function.

One of the powerful things about RL is that it can work with noncontinuous and hard-to-quantify objectives. We use the term reward instead of loss to imply the difference between these two situations. Generally, the types of objectives that RL can learn are referred to as nondifferentiable. As a result, these objectives can’t be learned using the same mathematical techniques like gradient descent, which we covered when describing how neural networks learn in chapter 4. We will explain how RLHF works specifically in a moment. The caveat lector of RL is that it can be computationally expensive and require a significant amount of data. RL is a notoriously challenging way to learn. It often works worse than other fine-tuning techniques like SFT because RL requires many more examples of the “right” and “wrong” way of doing things than other approaches, and since we are using human feedback to guide RLHF, the results are not always perfect. For example, in figure 5.5, RLHF cannot help an LLM understand basic instructions outside of what it has seen explicitly during RLHF training because it does not add any capability to perform basic logic, such as understanding the user’s request to avoid displaying information about dolphins, to the underlying model.

LLMs do not perform reasoning in the same way that we humans think of reasoning. You can get very far by collecting hundreds of millions of examples of “everything,” but the world is weird. We have little evidence that LLMs can reliably produce satisfying responses when something novel occurs. However, RLHF is the best so far for constraining how an LLM behaves. Despite its challenges, RL presents a way of learning that is not available with gradient-based methods that require differentiable objectives. Most importantly, ChatGPT has shown that RL can work in many cases. So let us dive deeper into how RLHF works.

</details>

### 5.2.3 微调：整体概览

SFT和RLHF是微调大语言模型的两种主要方法。SFT可以处理数千个文档或样本，而RLHF通常需要数万个示例。

数据较少时不应阻止你尝试，但如果数据确实不多，把时间花在编写更好的提示词上可能更划算。

更重要的是，SFT和RLHF并不互斥。它们都会修改模型的底层参数，你可以依次应用两种方法，从而获得各自的好处。

它们也并非当前仅有的微调方法。

例如，新的微调方法正在被开发中。

<details>
<summary>英文原文</summary>

SFT and RLHF are the two primary methods of fine-tuning an LLM. SFT can work with thousands of documents or samples, whereas RLHF often requires tens of thousands of examples. That should not stop you from investigating if you have less data, but if you have less data, it may be a better use of your time to develop better prompts. More importantly, SFT and RLHF are not mutually exclusive. They both modify the underlying parameters of the model, and you can apply one after the other to obtain the benefits of each approach. They are also not the only fine-tuning methods that currently exist. For example, new fine-tuning methods are being developed

</details>

![图 5.5 RLHF非常擅长让LLM避免已知的特定问题。然而，它并不能赋予模型处理新问题的工具。在用户询问迈阿密足球后，逻辑上接下来谈论迈阿密海豚队的倾向，违反了第一条避免提及海豚的要求。](assets/fig-5-5-781cb8a647.jpg)

*图5.5 RLHF非常擅长让LLM避免已知的特定问题。然而，它并不能赋予模型处理新问题的工具。在用户询问迈阿密足球后，逻辑上接下来谈论迈阿密海豚队的倾向，违反了第一条避免提及海豚的要求。*

从LLM中移除某些概念，以强制模型忽略其在训练后所学到的数据[6]。额外的模型修改技术将在未来几年内得到发展。所有这些技术很可能需要你进行一些数据收集，但总体工作量比从头构建一个LLM要少。

<details>
<summary>英文原文</summary>

that remove concepts from an LLM as a way of forcing a model to ignore data it has learned from after it has been trained [6]. Additional techniques for model alteration will be developed in the coming years. All will likely require you to do some data collection, but they will involve less work overall than trying to build an LLM from scratch yourself.

</details>

### 5.3 RLHF 的工作原理

为了说明RLHF的工作原理，我们将先介绍一个不完整的RLHF版本，解释它为何不起作用，然后再说明如何修正。在本节中，我们不会讨论RLHF使用的详细数学公式，因为从高层视角来看，它们并不能让你对RLHF产生特别深刻的理解。如果你想了解更具体的细节，我们建议在完成本章后，从“Implementing RLHF: Learning to Summarize with trlX”[7]开始入手。

<details>
<summary>英文原文</summary>

To describe how RLHF works, we will introduce an incomplete version of RLHF, explain why it does not work, and then explain how to fix it. In this section, we will not discuss the detailed math used by RLHF, as it would not give you any particularly great insights into RLHF from a high level. If you want to learn more about the nitty-gritty details, we recommend starting with ”Implementing RLHF: Learning to Summarize with trlX” [7] after you’ve completed this chapter.

</details>

### 5.3.1 从朴素 RLHF 开始

首先，我们来看看不完整且朴素的RLHF版本。我们已经讨论过RL如何通过不可微的目标进行学习。

因此，假设我们有一个人类评分员，他会用质量奖励来给LLM的输出打分，其中+1表示好的响应，-1表示不合格的响应。这个质量奖励只是我们分配给LLM输出的一种任意分数，用于表明某个例子比其他例子更好。

因此，如果用户请求LLM“讲个笑话”，LLM回复“需要多少只鸭子才能拧灯泡？”，我们可能会给这个（还算）不错的笑话打+1分。如果LLM反而输出了像“狗是邪恶的”这样的句子，我们会打-1分，因为它甚至没有尝试讲笑话。由于使用简单的+1和-1质量奖励来进行RL比较困难，我们会为RL算法添加额外信息，例如每个生成词元的概率。这样，RL算法就知道每个词元可能有多大的概率。整个过程如图5.6所示。

<details>
<summary>英文原文</summary>

First, let’s look at the incomplete and naive version of RLHF. We have discussed how RL can learn with nondifferentiable objectives. So let us assume that we have a human who will score an LLM’s output with a quality reward, where +1 indicates a good response and -1 is an inadequate response. This quality reward is simply an arbitrary score we assign to the output produced by the LLM to indicate that one example is somehow better than others. So if a user requests of an LLM, “Tell me a joke,” and the LLM produces a response of “How many ducks does it take to screw in a light bulb?” we might assign a score of +1 for a (reasonably) good joke. If the LLM instead produces a sentence like “Dogs are evil,” we will assign a score of -1 because it is not even attempting to make a joke. Because RL is difficult to do using simple quality rewards of +1 and -1, we will add additional information for the RL algorithm, such as the probabilities of each generated token. This way, the RL algorithm knows how probable each token may be. This whole process is summarized in figure 5.6.

</details>

![图 5.6 RLHF的一个朴素且不完整的版本。虚线表示从一个组件发送到另一个组件的文本。由于文本与梯度下降不兼容，因此必须使用更复杂的RL算法。这使得我们可以基于LLM输出的质量分数来调整LLM的权重。](assets/fig-5-6-9d81a060d3.png)

*图5.6 RLHF的一个朴素且不完整的版本。虚线表示从一个组件发送到另一个组件的文本。由于文本与梯度下降不兼容，因此必须使用更复杂的RL算法。这使得我们可以基于LLM输出的质量分数来调整LLM的权重。*

为何要向强化学习提供概率？

我们将每个 token 的概率提供给 RL 算法，这似乎有些奇怪。这样做有更深层的数学原因，本章不展开讨论。但直观上来说，一个好笑话往往需要误导或意外。如果序列中所有概率都很高（接近 1.0），那么它可能不是一个好笑话，因为太可预测了。

<details>
<summary>英文原文</summary>

It may seem odd that we are providing the RL algorithm with the probabilities of each token. There are deeper mathematical reasons why this is useful, which we will not get into in this chapter. But for some intuition, a good joke often requires misdirection or surprise. If all the probabilities of a sequence are high values (near 1.0), it is probably not a good joke because it’s too predictable.

</details>

大致而言，在整个自然语言处理中，生成优质文本是一种平衡：既要让内容合理（即可能发生），又不能让它过于合理（即重复）。

<details>
<summary>英文原文</summary>

Broadly, across natural language processing, producing good generated text is a balancing act between making something probable (i.e., likely to occur) and not making it too probable (i.e., repetitive).

</details>

### 5.3.2 质量奖励模型

我们将质量奖励描述为针对每个提示补全的人工评分。虽然实时手动给补全打分在技术上可行，但所涉及的工作量使得这种做法不合理。然而，人类反馈仍然通过质量奖励被纳入。相反，我们训练了一个神经网络作为奖励模型。这是通过让人类手动收集数十万个提示与补全对，并将其评为好或坏来实现的。这些评分成为用于训练奖励模型的标注数据，如图5.7所示。

<details>
<summary>英文原文</summary>

We described the quality reward as human-assigned scores for every prompt comple-tion. Although scoring completions manually in real time would technically work, it would be unreasonable due to the level of effort involved. However, human feedback is still incorporated via the quality reward. Instead, we train a neural network as a reward model. This is accomplished by having people manually collect hundreds of thousands of prompt and completion pairs and scoring them as good or bad. These scorings become the labeled data used to train the reward model, as shown in figure 5.7.

</details>

![图 5.7 奖励模型的训练方式类似于标准的监督分类算法。一个神经网络（可以是LLM本身，也可以是像卷积神经网络或循环神经网络这样的更简单的网络）被训练来预测人类如何对提示-完成对进行评分。由于神经网络是可微的，这种训练是可行的，从而提供了一个在RLHF](assets/fig-5-7-47a03b8235.png)

*图5.7 奖励模型的训练方式类似于标准的监督分类算法。一个神经网络（可以是LLM本身，也可以是像卷积神经网络或循环神经网络这样的更简单的网络）被训练来预测人类如何对提示-完成对进行评分。由于神经网络是可微的，这种训练是可行的，从而提供了一个在RLHF中充当“人类”的工具。*

收集数十万条带评分的提示和完成对是昂贵但可行的（例如，https://huggingface.co/datasets/Anthropic/hh-rlhf），尤其是在使用像 Mechanical Turk（https://www.mturk.com/）这样的众包工具时。这需要大量人工整理，但比用于创建初始基础模型的数十亿词元要小几个数量级。这些RLHF数据集必须足够大，因为你需要覆盖用户可能提出的各种场景、问题和请求。正如我们在图5.5的海豚示例中看到的，RLHF往往适用于相对简单和已知的主题。因此，处理不同情境的广度直接来源于微调数据的广度。

<details>
<summary>英文原文</summary>

Collecting hundreds of thousands of scored prompts and completion pairs is expen-sive but doable (e.g., https://huggingface.co/datasets/Anthropic/hh-rlhf), especially when using crowd-sourcing tools like Mechanical Turk (https://www.mturk.com/). That is a lot of data to curate manually but orders of magnitude smaller than the billions of tokens used to create the initial base models. These RLHF datasets must be large because you must cover many scenarios, questions, and requests that a user might provide. As we already saw in figure 5.5 with the dolphin example, RLHF tends to work for relatively straightforward and known topics. So breadth in handling different situations comes directly from breadth in the fine-tuning data.

</details>

注意：我们一直以+1/-1为例来说明提供质量奖励，因为它最容易描述。由于RL不需要梯度，你可以使用任何与问题相关的分数。使用排名分数（即比较给定提示的多个补全并从优到劣排序）更流行且更有效，因为你可以同时对多个补全进行评分。无论如何，提供正面和负面反馈的本质是相同的。

<details>
<summary>英文原文</summary>

NOTE We have been using +1/-1 as the example of providing a quality reward because it is the easiest to describe. Since RL does not need gradients, you can use any score relevant to your problem. Using a ranking score, where you compare multiple completions for a given prompt and rank them from best to worst, is more popular and more effective because you are grading multiple completions against each other simultaneously. Regardless, providing positive and negative feedback remains fundamentally the same.

</details>

### 5.3.3 相似却不同的RLHF目标

一旦训练好奖励模型，你就可以为RLHF流程创建并评分任意数量的提示。

人类反馈被嵌入到奖励模型中，现在可以分发、并行化和复用。

唯一剩下的问题是，当前朴素的RLHF版本只激励最大化质量奖励，而这不是RL必须关注的唯一目标。

结果，模型会随时间退化，产生胡言乱语和无意义的输出，这些输出质量不高，对任何读者都没有价值。这种退化与一种称为对抗性攻击的现象相关——只需对输入做出微小改变，就能出人意料地轻松欺骗神经网络做出荒谬决策。对抗性机器学习（AML）发展迅速，并且有其自身的复杂性深渊，所以我们把讨论留给其他人[8]。但我们在图5.6中描述的朴素RLHF实现本质上是对LLM执行对抗性攻击，因为它只专注于最大化质量奖励，而不是对用户有用。本质上，这是古德哈特定律在AI/ML中的体现：“当一个指标成为目标时，它就不再是一个好指标。”为了解决这个问题，我们必须给RL算法增加第二个目标。

我们将计算原始基础LLM输出与微调后LLM输出之间相似性的第二个奖励。概念上，这个奖励可以看作当微调LLM产生更好输出时的奖励，类似于原始LLM的行为方式。它防止模型因过于新颖而偏离轨道。从根本上说，我们希望微调LLM生成的输出以原始LLM最初观察到的训练数据为基础。我们不希望微调后的模型变得过于创意，以至于产生胡言乱语。这个奖励被添加到RL算法中以稳定微调过程。图5.8展示了RLHF的完整工作方式。

<details>
<summary>英文原文</summary>

Once you have trained a reward model, you can create and score as many prompts as you desire for the RLHF process. The human feedback is baked into the reward model and can now be distributed, parallelized, and reused. The only remaining problem is that the current naive version of RLHF is incentivized purely to maximize the quality reward, which is not the sole goal RL must focus on. As a result, the model will start to degrade over time by producing gibberish and nonsensical outputs that are not high quality and would not be valuable to any reader. This degradation is related to a phenomenon called adversarial attacks, where it is surprisingly easy to trick a neural network into absurd decisions with relatively minor changes to the input. Adversarial machine learning (AML) is fast evolving and has its own rabbit hole of complexity, so we’ll defer that discussion to other folks [8]. But the naive implementation of RLHF we describe in figure 5.6 essentially performs an adversarial attack against an LLM because it will focus only on maximizing the quality reward, not on being useful to the user. Essentially, this is Goodhart’s law happening to AI/ML: “When a measure becomes a target, it ceases to be a good measure.” To address this problem, we must add a second objective to the RL algorithm. We will calculate a second reward for the similarity between the original base LLM’s output and the fine-tuned LLM’s output. Conceptually, this reward can be considered a reward when the fine-tuned LLM produces better output, similar to how the original LLM behaved. It prevents the model from going off the rails by getting too novel. Fundamentally, we want the generated output of the fine-tuned LLM to be grounded by the training data initially observed by the original LLM. We don’t want the fine-tuned model to get so creative that it generates nonsense. This reward is added to the RL algorithm to stabilize the fine-tuning. Figure 5.8 provides the complete picture of how RLHF works.

</details>

![图 5.8 RLHF完整版本。虚线代表文本，需要强化学习来更新参数。原始LLM是未经任何修改的基础模型，而待微调的LLM虽以基础模型为起点，但会进行调整以提升输出质量。相似度奖励组件和质量奖励组件均接收词概率作为输入，以提高计算效果。强化学习通过融合质](assets/fig-5-8-79eacad7df.png)

*图5.8 RLHF完整版本。虚线代表文本，需要强化学习来更新参数。原始LLM是未经任何修改的基础模型，而待微调的LLM虽以基础模型为起点，但会进行调整以提升输出质量。相似度奖励组件和质量奖励组件均接收词概率作为输入，以提高计算效果。强化学习通过融合质量评分和相似度评分来调整参数。*

一个学会生成乱码输出的模型会因缺乏相似性而受到高惩罚，从而阻止模型变得过于不同。一个产生完全相同输出的模型会得到低质量分数，从而阻止其缺乏变化。两者的平衡很好地实现了“金发姑娘效应”，让模型有足够的灵活性进行变化，同时不至于失去其类人输出。

<details>
<summary>英文原文</summary>

A model that learns to produce gibberish output would receive a high penalty for lack of similarity, discouraging the model from becoming too different. A model that produces the exact same outputs will receive a low quality score, discouraging a lack of change. The balance of both does an excellent job of achieving a Goldilocks effect that allows the model enough flexibility to change without causing it to lose its human-like output.

</details>

![图 5.9 除了微调，你还可以通过更改训练数据、修改基模型训练过程，或编写代码处理特定情形来改变模型输出，从而改变模型行为。](assets/fig-5-9-44207cd52f.png)

*图5.9 除了微调，你还可以通过更改训练数据、修改基模型训练过程，或编写代码处理特定情形来改变模型输出，从而改变模型行为。*

微调是改变大语言模型行为的主要手段，但微调并非万无一失，也不是行为改变的唯一途径。我们之所以关注微调，是因为RLHF在让大语言模型产生超越简单下一个词预测的行为方面具有价值。

<details>
<summary>英文原文</summary>

Fine-tuning is the dominant means of altering the behavior of an LLM, but fine-tuning is not foolproof and is not the only place where behavior changes can occur. Our focus on fine-tuning is based on the value of RLHF in producing LLM behaviors beyond simple next-token prediction.

</details>

如图5.9所示，LLM行为可被修改的其他三个阶段，对用户而言并不容易直接触及。不过，此处我们将简要回顾这些阶段，并补充一些您应当了解的关键细节，以求完整。这些因素有助于理解通过微调难以实现的目标，以及您可能需要向LLM提供商探究的问题范围。

<details>
<summary>英文原文</summary>

The other three stages where LLM behavior can be modified, described in figure 5.9, are not easily accessible to you as a user. However, we will briefly review the other stages now, along with some key details you should know for completeness. These factors can help you understand what is challenging to achieve by fine-tuning and the scope of questions you might want to investigate in your LLM provider.

</details>

*[未译]* 5.4.1 Altering training data

### 训练数据隐私

在训练或微调大语言模型时，隐私保护是一个重要关切。通常，通过特殊方式构造模型输入，有可能重建模型的训练数据。

在某些情况下，已证明大语言模型会生成与训练数据完全一致的文本片段。

如果训练数据包含私人信息，如个人身份信息（PII）、个人健康信息（PHI）或其他敏感数据，这就成了问题。

模型用户可能在不经意间输入提示词，导致模型逐字泄露这些数据。

算法初始训练阶段是缓解部分隐私问题的理想时机，可通过差分隐私（DP）技术实现。

DP 较为复杂，若想深入学习，推荐阅读《Programming Differential Privacy》[13] 一书。

简而言之，DP 在模型训练过程中添加精心构造的随机噪声，为数据隐私提供可证明的保证。

DP 并非万能，但相比当前大多数算法，它能提供更强的保护。

那么，为什么不是所有人都这样做呢？添加噪声自然会降低结果质量。

大规模训练成本高昂，每次训练花费数十万到数百万美元。

如果为了正确设置隐私参数需要额外进行10倍的训练，就会面临数百万到数千万美元的成本问题。

但随着DP技术逐年改进，我们预计它未来会越来越普及。

<details>
<summary>英文原文</summary>

must be a significant concern when training or fine-tuning LLMs. Generally, it is possible to reconstruct a model’s training data by crafting inputs into a model in a special way. In some cases, LLMs have been shown to generate the exact passages on which they were trained. This is problematic if the training data contains private information, such as personally identifiable information (PII), private health information (PHI), or some other class of sensitive data. A user of a model could, perhaps unwittingly, provide a prompt that reveals this data verbatim. Initial training of an algorithm is an ideal place to mitigate some of these privacy concerns by using a technique known as differential privacy (DP). DP is complex, so if you want to learn more, we recommend the book Programming Differential Privacy [13]. In short, DP adds a carefully constructed amount of random noise to provide provable guarantees about data privacy in the model training process. DP does not handle everything, but it provides much more protection than what is available with most algorithms today. So why hasn’t everyone done just that? Well, adding noise naturally tends to reduce the quality of the result. Large training runs are expensive, costing hundreds of thou-sands to millions of dollars each. If you had to do 10× more training runs to set your privacy parameters correctly, you would have a million to tens-of-millions-of-dollars problem. But with DP becoming better every year, we suspect it will become more prevalent over time.

</details>

### 5.4.3 修改输出

最后，我们可以检查生成的词元，并编写代码根据模型生成的词元组合来改变其行为。

微调之后，这是LLM使用者用来修改其行为的第二常见阶段。

在本章前面，我们讨论了LLM的一个常见需求：生成符合精确格式（如XML或JSON）的输出。

实现这类格式化要求是LLM常见的问题。任何一个预测失败都会导致无法生成有效输出。

你可以在图5.10中看到这种失败的例子：我们要求LLM补全一些Python代码，下一个词元应该是分号（;），但它错误地尝试了一个换行符（\n）。

<details>
<summary>英文原文</summary>

Finally, we can examine the tokens being produced and write code to change its behavior based on the combinations of tokens generated by the model. After fine-tuning, this is the second most likely stage that a consumer of LLMs will use to modify their behavior. Earlier in this chapter, we discussed a common need for LLMs to generate output that adheres to a precise format, such as XML or JSON. Implementing formatting requirements like these is a common problem with LLMs. Any single failed prediction results in a failure to generate valid output. You can see an example of this type of failure in figure 5.10, where we ask the LLM to complete some Python code; the next token should be a semicolon (;), but it erroneously attempts a newline (\ n) instead.

</details>

![图 5.10 通过编写执行格式规范的代码，可以在LLM生成输出时捕获无效输出。一旦检测到，让LLM生成下一个最可能的token，直到找到有效输出，是一种简单的改进方法。](assets/fig-5-10-6bb05dd5c7.png)

*图5.10 通过编写执行格式规范的代码，可以在LLM生成输出时捕获无效输出。一旦检测到，让LLM生成下一个最可能的token，直到找到有效输出，是一种简单的改进方法。*

有多种工具（例如 `https://github.com/noamgat/lm-format-enforcer`）可以在 LLM 解码步骤中指定严格的格式。如果这些工具检测到解析错误，它们会立即重新生成最后一个 token，直到产生有效的输出。

当然还可以采用更复杂的方法来选择下一个 token。但重要的启示在于，我们可以利用中间输出，在生成完整输出之前就做出决策。即使是简单的老式“通过/不通过”列表，也是捕捉不良行为的有用工具。你无需真正实时地将输出传递给用户；完全可以引入人为延迟，这样在发送给用户之前，你能看到更多回复内容。这让你有时间对照不良语言过滤器或其他硬编码检查进行核对。如果匹配到了，就像图 5.10 中那样，你可以重新生成输出或终止用户会话。

<details>
<summary>英文原文</summary>

Various tools exist (e.g., https://github.com/noamgat/lm-format-enforcer) for speci-fying strict formats as a part of the LLM’s decoding step. If these tools detect a parse error, they immediately regenerate the last token until a valid output is produced. More sophisticated approaches to selecting the next token are possible. Still, the important lesson here is the ability to use the intermediate outputs to make decisions before generating the entire output. Even simple old-school “go/no-go” lists are valuable tools for catching bad behavior. You do not need to pass an output to the user in true real time; you can always introduce an artificial delay so that you can see more of the response before sending it to the user. This gives you time to chat against bad language filters or other hard-coded checks. If a match occurs, just like in figure 5.10, you can regenerate an output or abort the user’s session.

</details>

### 5.5 将LLM集成到更大的工作流中

至此，本章已介绍了一些操控LLM以生成更理想、更一致输出的基本方法。目前，我们关注的技术都直接作用于LLM本身，包括提示工程、训练数据操作或对基座模型进行微调。本节将探讨如何将LLM的输入和输出整合到多步操作链中，从而定制LLM的输出结果，实现更具针对性的效果。这一领域发展迅速，因此我们将简要介绍一个将LLM融入更广泛信息检索工作流的具体实例，然后讨论一个通用工具，展示如何通过与LLM的多次交互来自定义其输出。

<details>
<summary>英文原文</summary>

At this point in the chapter, we have covered some basic approaches to manipulating an LLM to produce more desirable and consistent outputs. So far, we have focused on techniques that involve the LLM itself, whether through prompting, manipulating training data, or fine-tuning a base model. In this section, we will explore how to tailor the output produced by an LLM by integrating the inputs and outputs of LLMs into multistep chains of operations to achieve more tailored results. This space is quickly evolving, so we will briefly cover one concrete example of integrating an LLM into a broader information retrieval workflow and then discuss a general-purpose tool to show you how to customize LLM outputs using multiple interactions with an LLM.

</details>

### 5.5.1 使用检索增强生成定制LLM

检索增强生成（RAG）是一种技术，它能让我们利用LLM生成答案，同时降低产生无意义或错误解释的可能性。RAG名称中的“检索”一词为你理解该技术的运作方式提供了有益的提示。当用户向RAG系统提供输入时，系统会利用LLM创建一个查询，该查询随后在包含文档索引的搜索引擎上运行。根据用例不同，这个索引可以是通用信息索引（如谷歌），也可以是特定主题的索引（如汽车营销材料集）。作为对查询的响应，搜索引擎会生成相关文档列表。然后，RAG系统利用LLM从这些文档中提取信息，以生成更好的答案。为此，RAG系统将检索到的文档内容与原始用户查询结合起来，为LLM创建一个综合提示，从而产生更好的响应。这种方法往往效果很好，因为我们不再要求LLM基于其训练或微调数据生成响应，而是要求LLM通过总结一组与普通搜索引擎查询相关的文档来生成对输入的响应，并将这组相关文档提供给LLM，供其从中提取答案。换句话说，我们是在帮助LLM聚焦于正确回答特定问题所需的数据。我们在图5.11中描述了这个过程，并将其与目前介绍的标准LLM用例进行了比较。到目前为止，RAG方法最显著的两个优势如下：

<details>
<summary>英文原文</summary>

Retrieval augmented generation (RAG) is a technique that allows us to produce answers from an LLM while reducing the likelihood of generating nonsensical or otherwise errant explanations. The “retrieval” component of the RAG moniker should give you a helpful hint as to how the technique operates. When a user provides input to a RAG system, it uses an LLM to create a query that is run against a search engine that contains an index of documents. Depending on the use case, this might be an index of general information, such as Google, or a subject-specific index, such as a collection of automotive marketing materials. In response to the query, the search engine generates a list of relevant documents. The RAG system then uses the LLM to extract information from those documents to generate better answers. To do this, the RAG system combines the contents of the retrieved documents with the original user query to create a comprehensive prompt for the LLM that will result in a better response. This method tends to work well because instead of asking an LLM to generate a response based on its training or fine-tuning data, we are now asking the LLM to generate a response to input by summarizing a set of documents relevant to a regular old search engine query and providing that set of relevant documents from which to draw its answers to the LLM. In other words, we’re helping the LLM focus on the data it needs to properly answer a given question. We describe this process in figure 5.11 and compare it with the normal LLM use cases we have described so far. The two most significant benefits of the RAG approach thus far are as follows:

</details>

RAG 系统的输出因其基于文档索引中的特定来源，所以更准确、更符合事实，或者对用户的原始问题更有帮助。

LLM 可以生成对其生成回答时所使用的源文档的引用或参考，使用户能够对照原始来源材料进行验证或比对。

<details>
<summary>英文原文</summary>

The output of a RAG system is more accurate, factually correct, or otherwise useful to the user’s original question because it is based on specific sources contained in a document index.

The LLM can generate citations or references to the source documents used to produce its responses, allowing users to validate or correlate against the original source material.

</details>

后者关于引用的问题尤为重要。RAG 并不能解决 LLM 的所有问题，因为在 RAG 系统中，最终输出仍然由 LLM 生成。LLM 仍然可能因为无法找到或不存在的内容而产生错误或幻觉。LLM 也可能无法准确捕捉或

<details>
<summary>英文原文</summary>

The latter point regarding citations is particularly important. RAG will not solve all of LLMs’ problems because the LLM still generates the final output in a RAG system. The LLM may still produce errors or hallucinations due to content that it cannot find or that doesn’t exist. It is also possible that the LLM will not accurately capture or

</details>

### 常规LLM使用
RAG风格LLM

1. 用户的问题会与搜索引擎或某种数据库进行比对。

<details>
<summary>英文原文</summary>

1. The user’s question is checked against a search engine or database of some form.

</details>

![图 5.11 图 5.11](assets/fig-5-11-27edf530b9.png)

*图5.11*

RAG 模型无法代表其所使用的任何源文档中的内容。因此，RAG 方法的效用直接与其执行搜索的质量以及返回的文档质量相关。归根结底，如果无法针对你的问题构建一个有效的搜索引擎，就无法构建一个有效的 RAG 模型。

<details>
<summary>英文原文</summary>

represent the content of any of the source documents it uses. As a result, the utility of the RAG approach is directly related to the quality of the search it performs and the documents that are returned. The bottom line is that if you can’t build an effective search engine for your problem, you can’t build an effective RAG model.

</details>

在思考大语言模型时，有一个重要方面需要关注，即上下文大小。大语言模型的上下文大小决定了它在单次完成请求中能够计算的token数量。你可以将其视为大语言模型在接收提示词输入时能够查看的数据量。例如，GPT-3的上下文大小为2,048个token。然而，在聊天机器人中，上下文通常用于存储整个对话的实时记录，包括大语言模型的输出。如果你与GPT-3的对话长度超过2,048个token，你会发现GPT-3常常会忘记对话早期讨论的一些内容。

<details>
<summary>英文原文</summary>

Context size When thinking about LLMs, it is important to consider one aspect of LLMs known as the context size. The context size of the LLM determines how many tokens it can computationally handle in a single request for completions. You can think of it as the amount of data that an LLM is able to look at when receiving input in the form of a prompt. For example, GPT-3 has a context size of 2,048 tokens. However, in chatbots, for example, the context is often used to hold a running transcript of the entire conversation, including any LLM outputs. If you have a conversation with GPT-3 that goes beyond 2,048 tokens in length, you’ll find that GPT-3 often loses track of some of the things discussed early on in the chat.

</details>

上下文大小是RAG使用的一个促进和限制因素。如果RAG系统检索一整本书供LLM消化，那么LLM将需要巨大的上下文大小来

<details>
<summary>英文原文</summary>

Context size is an enabling and limiting factor for RAG use. If a RAG system retrie-ves an entire book for your LLM to digest, your LLM will require a huge context size to

</details>

能够使用它。否则，LLM只能消费检索文档的第一部分（不超过LLM的上下文大小），从而可能遗漏信息。因此，上下文大小是选择模型时需要考虑的重要操作特性。如今一些模型，例如X的Grok，可以处理多达128,000个令牌的上下文大小。虽然像Grok这样的大上下文大小提高了LLM能消费的硬上限，但处理更大上下文所带来的大量输入时LLM的有效性仍是一个活跃的研究领域。

<details>
<summary>英文原文</summary>

(continued) be able to use it. Otherwise, the LLM can only consume the first part of a retrieved document (up to the LLM’s context size) and may miss information. As a result, context size is an important operational characteristic you should consider when choosing a model. Some models today, such as X’s Grok, can handle up to 128,000 tokens as their context size. While large context sizes like Grok’s increase the hard limit of what an LLM can consume, the effectiveness of LLMs when dealing with large amounts of input enabled by larger context sizes is still an active area of study.

</details>

你可能注意到，在图5.11中我们必须创建一个新提示。我们添加了前缀“回答问题：”和后缀“使用以下信息：”。理论上，通过调整这个提示，你可以获得更好的结果。你可能会想到添加一些指令，比如“忽略任何与原始问题无关的以下信息。”这些想法开始涉及提示工程，即调整和修改输入LLM的文本以改变其行为的实践，正如我们之前在第四章中讨论的那样。

提示工程确实有用，是一种结合多次调用LLM以改进结果的好方法。例如，你可以尝试通过让LLM重写问题来改进搜索结果。（这个讨论涉及信息检索的一个经典领域，称为查询扩展，如果你希望了解更多这个话题的话。）然而，提示工程可能非常脆弱：任何对LLM的更新都可能改变哪些提示有效或无效，而且必须重写每一个提示会很麻烦——尤其是当你涉及更复杂的东西，比如RAG模型或更高级的模型。

<details>
<summary>英文原文</summary>

You may notice in figure 5.11 that we have to create a new prompt. We added the prefix “Answer the question:” followed by the postfix “Using the following information:” Hypothetically, you could obtain better results by tweaking this prompt. You may get thoughts about adding some instructions like “Ignore any of the following information if it is not relevant to the original question.” These ideas are starting to get into prompt engineering, the practice of tweaking and modifying the text going into an LLM to change its behavior, as we talked about earlier in chapter 4. Prompt engineering is indeed useful and a good way to combine multiple calls to an LLM to improve results. For example, you could try to improve your search results by asking the LLM to rewrite the question. (This discussion touches on a classic area of information retrieval called query expansion, if you wish to learn more on the topic.) However, prompt engineering can be very brittle: any update to an LLM may change what prompts do or don’t work, and it would be a pain to have to rewrite every prompt—especially as you get into anything more complex, like a RAG model or something even more sophisticated.

</details>

### 5.5.2 通用LLM编程 尽管仍然

如今，我们已经开始看到将LLM作为自定义应用程序组件构建的编程库和其他软件工具。

我们特别中意的一个是DSPy（https://dspy.ai），它能够简化构建和维护试图修改LLM输入和输出的程序。

一个好的软件库会隐藏那些妨碍生产力的细节，而DSPy在抽象化围绕LLM使用的以下任务方面做得很好：

<details>
<summary>英文原文</summary>

new, we are already starting to see programming libraries and other software tools built using LLMs as a component of custom applications. One we particularly like is DSPy (https://dspy.ai), which can make it easier to build and maintain programs that attempt to alter the inputs to and outputs of an LLM. A good software library will hide details that get in the way of productivity, and DSPy does a good job of abstracting away the following tasks around LLM usage:

</details>

集成正在使用的特定LLM；实现常见的提示模式；根据你期望的数据、任务和LLM组合调整提示。

<details>
<summary>英文原文</summary>

Integrating the specific LLM being used Implementing common patterns of prompting Tweaking the prompts for your desired combination of data, task, and LLM.

</details>

这不是一本编程书，因此全面讲解DSPy超出了范围。但是，了解DSPy如何用于实现我们在5.5.1节中描述的RAG模型仍然具有启发性。这需要选择要使用的LLM（本例中为GPT-3.5）以及信息数据库（维基百科很合适），并定义RAG算法。DSPy的工作原理是定义一个所有组件都使用的默认LLM和数据库（除非你干预），这使得分离和替换所使用的部分变得容易。这个过程如下列表所示。

<details>
<summary>英文原文</summary>

This is not a coding book, so a full tutorial on DSPy is out of scope. But it is illustrative to look at the ways DSPy can be used to implement the RAG model we described in section 5.5.1. It will require that we pick an LLM to use (GPT-3.5, in this case), as well as a database of information (Wikipedia will work well), and define the RAG algorithm. DSPy works by defining a default LLM and database used by all components (unless you intervene), making it easy to separate and replace the parts being used. This process is shown in the following listing.

</details>

### 清单 5.1 DSPy 中最简单的 RAG

### 使用 OpenAI 的

GPT-3.5（可替换为其他在线或本地LLM）import dspy llm = dspy.OpenAI(model='gpt-3.5-turbo') 使用ColBERTv2算法对维基百科副本进行向量化 similarity_and_database = dspy.ColBERTv2(

<details>
<summary>英文原文</summary>

GPT-3.5, which can be swapped out with other online or local LLMs import dspy llm = dspy.OpenAI(model='gpt-3.5-turbo') Uses the ColBERTv2 algorithm to vectorize a copy of Wikipedia similarity_and_database = dspy.ColBERTv2(

</details>

使用我们刚刚创建的 LLM 和文档数据库 dspy.settings.configure(

<details>
<summary>英文原文</summary>

Uses the LLM and document database we just created dspy.settings.configure(

</details>

检索数据库中三个最相关的文档 self.retrieve = dspy.Retrieve(

<details>
<summary>英文原文</summary>

Searches for the three most relevant documents from the database self.retrieve = dspy.Retrieve(

</details>

指定一个“签名”字符串，用于定义LLM的输入和输出：self.generate_answer = dspy.Prediction(

<details>
<summary>英文原文</summary>

Specifies a “signature” string, which defines the inputs and output of the LLM self.generate_answer = dspy.Prediction(

</details>

这段代码将前述关于LLM和数据库的选择设为默认值，使得替换OpenAI为其他在线LLM或本地模型（如Llama）同样简单。`class RAG(dspy.Module):` 类随后定义了RAG算法。初始化器仅包含两部分。

首先，我们需要一种基于向量化文档搜索字符串数据库的方法，该方法由ColBERTv2定义。它使用了一个更老的语言模型（也就是四年前，天哪，这个领域发展真快），但速度更快，以提升效率和性能。记住，较大的语言模型（即运行成本更高的模型）只需要检索到合理的文档即可。尽管ColBERTv2的效果可能不如GPT-3.5，但在大多数情况下，它足以帮你找到正确的文档。然后，`dspy.Retrieve` 使用这个默认数据库进行搜索，因此除了指定检索的文档数量外，无需其他设置。

<details>
<summary>英文原文</summary>

This code sets the aforementioned choices in LLMs and databases as the defaults, making it just as easy to replace OpenAI with another online LLM or a local one such as Llama. The class RAG(dspy.Module): class then defines the RAG algorithm. The initializer only has two parts.

First, we need a way to search a database of strings based on vectorized documents, which is defined with ColBERTv2. It uses an older—as in just four years ago (wild how fast the field is moving)—but much faster language model for speed and efficiency. Remember, the larger language model (that is, the more expensive to run) just needs reasonable documents to be retrieved. While ColBERTv2 probably won’t do as good a job as GPT-3.5, it is more than good enough to get you the right documents most of the time. The dspy.Retrieve then uses this default database for searching, so there is no need to specify anything more than how many documents to retrieve.

</details>

其次，我们需要将问题和文档组合成一条查询，发送给大语言模型。在DSPy中，提示词对我们来说是抽象的。相反，我们编写DSPy所谓的签名（signature），你可以将其视为函数的输入和输出。这些输入和输出应赋予有意义的英文名称，以便DSPy为你生成良好的提示词。（在底层，DSPy使用语言模型来优化提示词！）在本例中，我们有两个输入（question和relevant_documents），用逗号分隔。符号->用于表示输出的开始，这里我们只有一个输出：问题的答案。

<details>
<summary>英文原文</summary>

Second, we need to combine the questions and documents into a query for the LLM. In DSPy, the prompt is abstracted away from us. Instead, we write what DSPy calls a signature, which you can think of as the inputs and outputs of a function. These should be given meaningful English names so that DSPy can generate a good prompt for you. (Under the hood, DSPy uses a language model to optimize prompts!) In this case, we have two inputs (question and relevant_documents) separated by a comma. The -> is used to denote the start of the outputs, of which we have only one: the answer to the question.

</details>

注：DSPy 的签名支持一些基本类型。例如，你可以通过在字符串中标注 "question, relevant _documents -> answer:int" 来强制答案必须为整数。该命令将应用我们在图5.10中学到的相同技术，在出错时重新生成。

<details>
<summary>英文原文</summary>

NOTE DSPy supports some basic types in signatures. For example, you can enforce that the answer must be an integer by denoting ”question, relevant _documents -> answer:int” in the string. This command will apply the same technique to regenerating on errors that we just learned about in figure 5.10.

</details>

这就是定义我们的 RAG 模型所需的全部操作！在 forward 函数中调用对象并传出，但你可以根据需要修改这段代码来添加更多细节。你可以将所有文本转为小写、运行拼写检查器，或在此处使用任何你想要的代码。这种方法让你能够将编程规则与 LLM 混合搭配使用。你还可以轻松修改 RAG 定义以纳入新的约束条件，并编写代码让 LLM 执行验证。

更重要的是，DSPy 支持使用训练/验证集来更好地调整提示、微调本地 LLM，并帮助你创建一个经过经验测试、改进和量化的模型，从而无需花费大量时间处理 LLM 的特定细节即可实现目标。尽早采用这类工具，你将获得一个更加稳健的解决方案，从而能更轻松地升级到更新的架构。

<details>
<summary>英文原文</summary>

That is all it takes to define our RAG model! The objects are called and passed out in the forward function, but you can modify this code to add additional details if you want. You can convert everything to lowercase, run a spell checker, or use whatever kind of code you want here. This approach lets you mix and match programming rules with LLMs.

You can also easily modify the RAG definition to include new constraints and write code to have an LLM perform validation. More importantly, DSPy supports using a training/validation set to tune the prompts better, fine-tune local LLMs, and help you create an empirically tested, improved, and quantified model to achieve your goals without having to spend a lot of time on LLM-specific details. Adopting tools like this early will give you a far more robust solution that allows you to upgrade to newer architectures more easily.

</details>

*[未译]* Summary

你可以通过四个环节干预并改变模型的行为：数据收集/分词、初始基座模型训练、基座模型微调，以及预测令牌拦截。这四个环节都很重要，但微调是大多数用户最有效的干预方式，能够降低成本并最大程度调整模型的目标。监督微调（SFT）是在更小的定制数据集上执行常规训练过程，有助于精炼模型对特定领域的知识。

基于人类反馈的强化学习（RLHF）需要更多数据，但它允许我们指定比“预测下一个令牌”更复杂的目标。在输出格式必须严格（如JSON或XML）的情况下，你可以使用语法检查器等现有工具来检测LLM的不正确输出。生成和语法检查可以循环运行，直到输出满足必要的语法约束。

<details>
<summary>英文原文</summary>

You can intervene to change a model’s behavior in four places: the data collection/tokenization, training the initial base model, fine-tuning the base model, and intercepting the predicted tokens. All four places are important, but fine-tuning is the most effective place for most users to make changes that lower the cost and provide the optimal ability to change the model’s goals. Supervised fine-tuning (SFT) performs the normal training process on a smaller bespoke data collection and is useful for refining the model’s knowledge of a particular domain.

Reinforcement learning from human feedback (RLHF) requires more data, but it allows us to specify objectives more complex than “predict the next token.” You can use existing tools like syntax checkers to detect incorrect LLM outputs in cases where the output format must be strict, such as for JSON or XML. Generation and syntax checking can be run in a loop until the output satisfies the necessary syntax constraints.

</details>

检索增强生成（RAG）是一种流行的方法，它通过搜索引擎或数据库首先找到相关内容，然后将其插入到提示中，从而增强LLM的输入。

像DSPy这样的编码框架开始出现，它们将特定的LLM、向量化和提示定义与针对特定任务修改LLM输入和输出的逻辑分离开来。这种方法允许你构建更可靠、可重复的LLM解决方案，能够快速适应新模型和新方法。

<details>
<summary>英文原文</summary>

Retrieval augmented generation (RAG) is a popular method of augmenting the input of an LLM by first finding relevant content via a search engine or database and then inserting it into the prompt.

Coding frameworks like DSPy are beginning to emerge that separate the specific LLM, vectorization, and prompt definition from the logic of how inputs and outputs from the LLM are modified for a specific task. This method allows you to build more reliable and repeatable LLM solutions that can quickly adapt to new models and methods.

</details>



---

<a id="ch6"></a>

## 第 6 章: 超越自然语言处理

6 超越自然语言处理

<details>
<summary>英文原文</summary>

6 Beyond natural language processing

</details>

### 本章涵盖

- Transformer 层在非文本数据上的工作原理

- 帮助 LLM 编写可运行的软件

- 调整LLM以理解数学符号

- Transformer 如何替换输入和输出步骤以处理图像

<details>
<summary>英文原文</summary>

- How transformer layers work on data other than text
- Helping LLMs to write working software
- Tweaking LLMs so they understand mathematical notation
- How transformers replace the input and output steps to work with images

</details>

虽然对自然语言进行建模是Transformer的主要用途，但机器学习研究人员很快发现，它们可以预测涉及数据序列的任何内容。Transformer将句子视为token序列，要么生成相关的token序列（例如将一种语言翻译成另一种语言），要么预测序列中的后续token（例如回答问题或充当聊天机器人时）。虽然序列建模和预测是解释和生成自然语言的有力工具，但自然语言并不是LLM唯一可以发挥作用的领域。除了人类语言之外，许多数据类型都可以表示为token序列。用于实现软件的源代码就是其中一个例子。源代码并非由你期望看到的英语单词和语法构成，而是用像Python这样的计算机编程语言编写的。源代码有自己的结构。

<details>
<summary>英文原文</summary>

While modeling natural language was the transformers’ primary purpose, machine learning researchers quickly discovered they could predict anything involving data sequences. Transformers view a sentence as a sequence of tokens and either produce a related sequence of tokens, such as a translation from one language to another, or predict the following tokens in a sequence, such as when answering questions or acting like a chatbot. While sequence modeling and prediction are potent tools for interpreting and generating natural language, natural language is the only domain where LLMs can be helpful.

Many data types, other than human language, can be represented as a sequence of tokens. Source code used to implement software is one example. Instead of the words and syntax you would expect to see in English, source code is written in a computer programming language like Python. Source code has its own structure

</details>

描述了软件开发者希望计算机执行的操作。与人类语言类似，源代码中的标记根据所使用的语言及其出现的上下文具有含义。与其说有什么不同，不如说源代码比人类语言具有更高度的结构性和特异性。带有歧义和含义微妙差异的编程语言将使计算机难以解释，也让其他人更难修改和维护。

源代码（或简称为“代码”，下文将如此称呼）只是LLM和Transformer处理非自然语言数据的一个例子。几乎任何可以重新表示为标记序列的数据都可以利用Transformer以及我们学到的关于LLM工作原理的诸多经验。本章将回顾三个逐渐远离自然语言的例子：代码、数学和计算机视觉。

这三种不同类型的数据（称为数据模态）都将需要对Transformer的输入或输出进行新的审视。然而，在所有情况下，Transformer本身保持不变。我们仍然会将多个Transformer层堆叠起来构建模型，并继续使用梯度下降训练这些Transformer层。代码与自然语言最为相似，因此不需要太多改动。不过，为了使代码LLM运行良好，我们将改变LLM输出生成后续标记的方式。接下来，我们将探讨数学领域，其中需要改变分词方式，以使LLM能够成功完成加法等基本运算。最后，对于计算机视觉（涉及处理图像以及执行目标检测和识别等任务），我们将同时修改输入和输出，展示如何通过完全替换标记的概念，将一种截然不同的数据类型转换为序列。我们在图6.1中展示了针对每种数据模态必须修改的LLM部分。

<details>
<summary>英文原文</summary>

that describes the operations a software developer wants a computer to perform. Like human language, the tokens in the source code have meaning according to the language used and the context in which they appear. If anything, source code is more highly structured and specific than human language. A programming language with shades of ambiguity and meaning would be challenging for a computer to interpret and harder for others to modify and maintain.

Source code, or simply “code” (which is how we’ll refer to it from here on), is just one example of how LLMs and transformers work with data that is not natural language. Almost any data you can recast as a sequence of tokens can use transformers and the many lessons we have learned about how LLMs work. This chapter will review three examples that become progressively less like natural language: code, mathematics, and computer vision.

Each of these three different types of data, known as data modalities, will require a new way of looking at a transformer’s inputs or outputs. However, in all cases, the transformer itself will remain unchanged. We will still stack multiple transformer layers on top of each other to build a model, and we will continue to train the transformer layers using gradient descent. Code, being the most similar to natural language, does not require too many changes. To make a code LLM work well, though, we will change how the outputs of the LLM generate subsequent tokens. Next, we will look at mathematics, where we need to change tokenization to get an LLM to succeed at basic operations such as addition. Finally, for computer vision, which concerns working with images and performing tasks such as object detection and identification, we will modify both the inputs and outputs, showing how you can convert a very different type of data into a sequence by replacing the concept of tokens entirely. We show the parts of LLMs that you must modify to work with each data modality in figure 6.1.

</details>

数学需要修改分词步骤。

<details>
<summary>英文原文</summary>

Math requires altering the tokenization step.

</details>

代码需要修改输出生成步骤。

<details>
<summary>英文原文</summary>

Code requires to altering the output generation step.

</details>

输出生成 Transformers 分词

<details>
<summary>英文原文</summary>

Output generation Transformers Tokenization

</details>

计算机视觉需要同时修改分词和输出步骤。

<details>
<summary>英文原文</summary>

Computer vision requires altering both the tokenization and output steps.

</details>

*图6.1 如果将LLM分解为三个主要组件——输入（分词）、变换（Transformers）和输出生成（去嵌入）——我们可以通过改变输入或输出组件中的至少一个来使用新的数据模态。同时，由于Transformer是通用的，在大多数用例中无需修改。*

<details>
<summary>英文原文</summary>

Figure 6.1 If we break an LLM into three primary components—input (tokenization), transformation (transformers), and output generation (unembedding)—we can use new data modalities by changing at least one of the input or output components. Meanwhile, the transformer does not require modification for most use cases because it is general-purpose.

</details>

### 6.1 大语言模型在软件开发中的应用

我们已经简要讨论过，LLMs能够编写软件的源代码。在第4章中，我们让ChatGPT编写了一段Python代码来计算数学常数π。接着，我们要求它将该代码转换为一种名为Modula-3的冷门语言。软件是人们最早发现LLMs能够提供帮助的领域之一，这是编程工作方式的自然结果。编程语言的设计初衷就是像文本一样供人类读写！因此，我们无需改变分词过程就能生成代码。我们讨论过的关于构建LLMs的一切，同样适用于代码和人类语言。

通过观察图6.2中ChatGPT对Python和Java两段相似代码的分词结果，我们可以印证这一点。这里，我们用灰色阴影来显示OpenAI分词器（https://platform.openai.com/tokenizer），它将代码分解为不同的词元。虽然同一个词元在两个示例中可能颜色不同，但我们可以关注分词器如何将代码拆分为词元以及两个示例之间的相似性。这些相似之处包括：每行代码的缩进、变量x和i（大多数情况下）、函数名和返回语句、以及像 `+=` 这样的运算符。这些相似性使得LLM更容易关联每段代码之间的相似性。

这些相似性还意味着，LLM在训练过程中可以在具有共同命名、语法和编码实践的编程语言之间共享信息。

软件开发者被鼓励使用有意义的变量名，以反映变量在其所编写程序中的角色或用途。像 `initValue` 这样的变量名会被拆分为 `init` 和 `Value` 两个词元，使用相同的词元来表示……

<details>
<summary>英文原文</summary>

We’ve already briefly discussed that LLMs can write source code for software. In chap-ter 4, we asked ChatGPT to write some Python code for calculating the mathematical constant 휋. Next, we asked it to convert that code into an obscure language called Modula-3. Software was one of the first things people discovered LLMs could help with as a relatively natural consequence of how programming works. Programming languages are designed to be read and written by humans like text! Consequently, we can generate code without changing the tokenization process. Everything we have discussed about constructing LLMs applies equally to code and human languages. We can see this by looking at ChatGPT’s tokenization of two similar code segments for Python and Java in figure 6.2. Here, we use shades of grey to show the OpenAI tokenizer (https://platform.openai.com/tokenizer), which breaks code into diffe-rent tokens. While the same token might have a different color in each example, we can focus on how the tokenizer breaks code into tokens and the similarities between both examples. These include things like The indentation for each line of code The x and i variables (in most cases) The function name and return statement The operators, such as += These similarities make it far easier for an LLM to correlate the similarity between each piece of code. The similarities also mean that the LLM shares information between programming languages with common naming, syntax, and coding practices during training.

Software developers are encouraged to use meaningful variable names that reflect a variable’s role or purpose in the programs they write. Variables named like initValue are broken up into two tokens for init and Value, using the same tokens to represent

</details>

![图 6.2 用Python（左）和Java（右）编写的两个类似代码示例。这些示例显示了字节对编码如何识别不同语言中的相似标记。框内表示单个标记。人类语言的标准分词方法在代码上也表现良好，因为代码与自然语言有许多相似之处。](assets/fig-6-2-448d4535b8.png)

*图6.2 用Python（左）和Java（右）编写的两个类似代码示例。这些示例显示了字节对编码如何识别不同语言中的相似标记。框内表示单个标记。人类语言的标准分词方法在代码上也表现良好，因为代码与自然语言有许多相似之处。*

自然语言文本中出现了单词“Value”的前缀“init”。因此，我们不仅通过相似的语法在编程语言之间共享信息，还通过变量名共享代码的上下文和意图。大语言模型也受益于程序员添加的代码注释，这些注释用于向自己或其他程序员描述代码的复杂部分。在图6.3中，我们重复了Java版本的代码，更改了变量名，并在函数顶部添加了一个描述性（但在实际中是不必要的）注释。

<details>
<summary>英文原文</summary>

natural language text where the prefix “init” of the word “Value” occurs. So not only do we share information between programming languages with similar syntax, but we also share information about the context and intention of code via variable names. LLMs also benefit from the code comments that programmers add to describe complex parts of the code for themselves or other programmers. In figure 6.3, we have the Java version repeated with a change in the variable name and a descriptive (but unnecessary in real life) comment at the top of the function.

</details>

![图 6.3 用Java编写的代码，包含一条描述代码功能的注释。因为（好的）代码（希望）有很多注释，所以自然语言和代码自然混合，供大语言模型获取信息。当变量名具有描述性时，模型更容易将代码信息与注释和变量名中描述的意图关联起来。](assets/fig-6-3-862aec302c.png)

*图6.3 用Java编写的代码，包含一条描述代码功能的注释。因为（好的）代码（希望）有很多注释，所以自然语言和代码自然混合，供大语言模型获取信息。当变量名具有描述性时，模型更容易将代码信息与注释和变量名中描述的意图关联起来。*

大多数情况下，代码和注释会得到相同的token，将人类语言和编程语言联系在一起，因为它们使用相同的表示形式。无论我们处理的是编程语言还是自然语言，我们都会得到相同的token和嵌入。其美妙之处在于，LLM会像人类程序员一样，复用自然语言信息来理解源代码的含义。

在每个案例中，我们看到tokenization对于代码来说并非完美。存在一些边缘情况，LLM的tokenizer无法将代码中的数据类型转换为相同的token。例如，可以看到函数参数中的(double的token与函数体中的double token处理方式不同。然而，这些差异类似于自然语言LLM中已经存在的问题，比如单词“hello ”、“hello.”和“hello!”周围的不同大小写和标点被解释为不同的token。既然LLM能够处理这些细微差异，那么它们也能处理代码中的相同问题。实际上，对于LLM来说，这个问题在代码中更容易处理，因为代码是区分大小写的，所以我们不需要担心像“hello”和“Hello”被不恰当地映射到不同token这样的文本情况。在代码中，“hello”和“Hello”将是不同且独特的变量或函数名。将它们视为不同的token是正确的，因为编程语言将它们视为不同的元素。

从应用角度来看，代码生成特别有趣，因为它提供了各种自我验证的机会。我们可以应用第5章中关于监督微调（SFT）和基于人类反馈的强化学习（RLHF）的所有经验，使LLM成为一个有效的编码智能体。

<details>
<summary>英文原文</summary>

In most cases, we get the same tokens between code and comments, linking hu-man and programming languages together since they use the same representation. Whether we are working with a programming language or natural language, we get the same tokens and embeddings. The beauty of this is that an LLM will reuse information about natural languages to capture the meaning of the source code, much like human programmers do.

In each case, we see that the tokenization is not perfect for the code. There are edge cases where the LLM’s tokenizer does not convert the data types in the code to the same token. For example, you can see that the token for (double in the function argument is handled differently from the token for double in the function body. However, these differences are similar to the problems we already see in LLMs for natural language, where different cases of punctuation around a word like “hello ”, “hello.”, and “hello!” are interpreted as different tokens. Since LLMs can handle these minor differences, it makes sense that they can also handle the same problem for code. The problem is, in many ways, easier for an LLM to handle in code because code is case sensitive, so we do not need to worry about textual situations like “hello” and “Hello” being inappropriately mapped to different tokens. In code, “hello” and “Hello” would be separate and distinct variable or function names. Treating them as separate tokens is correct because the programming language treats them as different elements.

Code generation is particularly interesting from an application perspective because of the various opportunities for self-validation. We can apply all the lessons on supervised fine tuning (SFT) and reinforcement learning with human feedback (RLHF) from chapter 5 to make an LLM an effective coding agent.

</details>

### 6.1.1 提升LLM处理代码的能力

改进LLM用于代码的第一步是确保初始训练数据中包含代码示例。

由于互联网的特性，大多数LLM开发者已经做到了这一点：代码示例在网上很常见，自然就进入了大家的训练数据集。

改进结果则成为应用SFT的机会，我们收集额外的代码示例，并在这些代码示例上微调我们的LLM。

像GitHub这样的开源代码库包含大量代码，使得获取大量代码变得尤为容易。

从GitHub等来源收集的代码构成了用于理解和生成代码的LLM微调数据集的基础。

更有趣的是使用RLHF来提升模型编写代码的实用性。

同样，有许多可用的工具和数据集，使得为编码助手构建一个不错的RLHF数据集成为可能。

像Stack Overflow这样的来源允许用户提出问题，提供他人回答问题的功能，并包含其他用户投票选出最佳答案的系统。

数据来源还包括像CodeJam这样的编程竞赛，它们为特定的编程问题提供了许多示例解决方案。

整合这些数据源的信息如图6.4所示。

与所有优秀的机器学习解决方案一样，如果你创建并标注针对自己任务的特定数据，就能获得最佳结果。

据传OpenAI在生成代码方面就是这么做的，他们雇佣承包商完成编码任务，作为为其系统创建数据的一部分[1]。

无论训练和微调数据是如何收集的，总体策略保持不变：使用标准分词器以及SFT和RLHF来打造一个专为生成代码而设计的LLM。

这一方法已成功用于生成Code Llama [2]和StarCoder [3]等LLM。

<details>
<summary>英文原文</summary>

The first step to improving an LLM for code is ensuring that code examples are present within the initial training data. Due to the nature of the internet, most LLM developers have already done this: code examples are frequent online and naturally make their way into everyone’s training datasets. Improving the results then becomes an opportunity to apply SFT, where we collect additional code examples and fine-tune our LLM on the given code examples. Open source repositories like GitHub, which contain significant volumes of code, make obtaining a large amount of code especially easy. Code collected from sources such as GitHub forms the basis of a fine-tuning dataset for LLMs that interpret and produce code. The more interesting case is using RLHF to improve a model’s utility for writing code. Again, there are many tools and datasets available that make it possible to build a decent RLHF dataset for a coding assistant. Sources like Stack Overflow allow users to enter questions, provide a facility for other people to give answers to these questions, and include a system where other users vote on the best answers. Data sources include coding competitions like CodeJam, which provide many example solutions to a specific coding problem. Incorporating information from data sources like these is shown in figure 6.4. Like all good machine learning solutions, you get the best results if you create and label your own data specific to your task. It is rumored that OpenAI did this for generating code, hiring contractors to complete coding tasks as part of creating the data for their system [1]. Regardless of how training and fine-tuning data is collected, the overall strategy remains the same: use standard tokenizers and SFT with RLHF to make an LLM tailored to generate code. This recipe has been used successfully to produce LLMs such as Code Llama [2] and StarCoder [3].

</details>

![图 6.4 为代码开发的LLM经过多轮微调。采用第4章所述的标准训练流程，首先生成一个基础LLM。通过大量代码进行有监督微调（SFT），得到一个擅长代码处理的LLM。再将RLHF作为第二轮微调步骤，进一步提升LLM生成代码的能力。](assets/fig-6-4-28bc964067.png)

*图6.4 为代码开发的LLM经过多轮微调。采用第4章所述的标准训练流程，首先生成一个基础LLM。通过大量代码进行有监督微调（SFT），得到一个擅长代码处理的LLM。再将RLHF作为第二轮微调步骤，进一步提升LLM生成代码的能力。*

### 6.1.2 验证LLM生成的代码

LLM在代码生成方面尤为有用，因为存在一个客观且易于执行的验证步骤：尝试将代码编译成可执行程序[4]。

在生成自然语言时，检查LLM输出的正确性颇具挑战，因为自然语言具有主观性。

目前尚无自动化手段可检验LLM输出的真实性或准确性。然而，在生成代码时，仅检查代码能否成功编译为可执行文件就是一个良好的第一步，并能捕获大部分错误代码。

一些商业产品更进一步，将编译器（将源代码转换为可执行文件的软件）和可视化工具等集成到其后端。

例如，ChatGPT可以在将代码返回给用户之前检查其能否编译。

如果代码未通过此验证步骤，ChatGPT将尝试为收到的提示生成不同的代码。

如果模型无法生成可编译的有效代码，它就会向用户发出警告。

除了检查代码能否编译之外，LLM还逐渐能够创建验证功能正确性的方法。

许多代码生成工具利用LLM生成单元测试，这些测试是向生成的代码提供样本输入并验证其产生正确结果的小型程序。

在某些情况下，这些能力要求开发者描述他们希望LLM生成的测试用例，然后LLM会创建一个初始实现作为进一步测试的起点。

代码尤为特殊，因为除了编译之外，还存在多种验证其输出的方式。

例如，代码编译必须等到LLM完成响应生成之后才能进行。

考虑到运行LLM成本高昂，且我们不想让用户等待太长时间才能获得输出，理想情况下LLM能在完成大量生成之前纠正错误。

再次运用第5章的经验，我们可以使用语法解析器在完成整个生成过程之前检查代码是否存在错误。如果输出代码的某些部分未通过基本语法检查，我们可以指示LLM仅重新生成该有问题的代码部分。图6.5展示了这一基本过程：LLM逐token进行检查，而不是等待生成完成后再通过编译来检查代码。语法检查比编译成本更低、速度更快，但它无法验证编译器能否将代码转化为可运行的可执行程序。

<details>
<summary>英文原文</summary>

LLMs are particularly useful for code generation because there is an objective and easy-to-run verification step: attempting to compile the code into an executable program [4]. When generating natural language, it is challenging to check the correctness of the output generated by an LLM because natural language can be subjective. There isn’t an automated way to check the truthfulness or veracity of the output generated by an LLM. However, when generating code, simply checking whether the code compiles successfully into an executable is a good first step and catches a large portion of the incorrect code. Some commercial products take this a step further and integrate tools such as compilers (software that transforms source code into executables) and visualization tools into their backend. For example, ChatGPT can check whether the code it writes compiles before returning it to the user. If the code doesn’t pass this verification step, ChatGPT will try to generate different code for the prompt it received. If the model cannot create valid code to compile, it will warn the user of this fact. Beyond checking whether code can compile, LLMs are increasingly able to create methods for validating functional correctness. Many code generation tools utilize LLM to generate unit tests, which are tiny programs that provide sample input into generated code and validate that it produces the correct result. In some cases, these capabilities require the developer to describe the test cases that they want the LLM to generate, and the LLM creates an initial implementation as a starting point for further testing. Code is particularly special because multiple ways exist to validate its output beyond just compilation. For example, code compilation can’t happen until the LLM finishes generating its response. Considering that LLMs are expensive to run, and we don’t want to keep a user waiting too long for output, it would be ideal if the LLM could correct errors before completing a large generation. Again, applying the lessons from chapter 5, we can use a syntax parser to check whether the code is incorrect before completing the entire generation process. If portions of the output code fail a basic syntax check, we can instruct the LLM to regenerate just that faulty portion of code. We show the basic process behind this in figure 6.5, where the LLM performs a check on a per-token basis instead of waiting for the generation to complete before checking the code using compilation. The syntax check is less expensive and can happen faster than compilation, but it does not validate that a compiler can turn the code into a working executable program.

</details>

![图 6.5 一个Python代码示例，其中当前已生成token if(A > B)。如果LLM生成的下一个token是换行符，则会产生语法错误，因为if语句必须以冒号结尾才是有效的。对每个新token运行语法检查，可以捕获此错误，并强制LLM选择一个不](assets/fig-6-5-c84e629e44.png)

*图6.5 一个Python代码示例，其中当前已生成token if(A > B)。如果LLM生成的下一个token是换行符，则会产生语法错误，因为if语句必须以冒号结尾才是有效的。对每个新token运行语法检查，可以捕获此错误，并强制LLM选择一个不会导致语法错误的替代token。*

### 6.1.3 通过格式化改进代码：使用

用于语法检查的解析器和生成可执行文件的编译器，使得将LLM适配到代码生成这一新问题领域变得容易得多。

不过，还有一个额外技巧很有用。我们可以使用称为代码格式化器（程序员也称之为linter）的工具来改变分词方式并提升性能。

问题在于，编写功能相同但分词方式不同的代码有多种可能。

应用linter调整源代码格式，有助于消除功能等价但形式不同的两段代码之间的差异。

虽然重新格式化代码并非代码LLM良好运行的必要条件，但它有助于避免可能出现的非必要冗余。

例如，考虑Java编程语言，它使用花括号来标志程序中新作用域的开始和结束。

各种形式的空白符现在无关紧要，但会被以不同方式分词，尤其是对于仅使用单行代码的作用域，花括号是可选的！

图6.6展示了对于执行相同功能的代码，存在哪些不同的合法格式，以及理想情况下我们如何将代码转换为单一规范表示。

<details>
<summary>英文原文</summary>

parsers for syntax checking and compilers to produce working executables makes it far easier to adapt LLMs to the new problem domain of generating code. However, one additional trick is helpful. We can use tools known as code formatters (also known by programmers as linters) to change tokenization and improve performance. The problem is that there can be many ways to write code that performs the same functions yet is tokenized differently. Applying a linter to adjust source code for-matting helps remove differences between two functionally equivalent, yet different pieces of code. While reformatting code is not a requirement to make code LLMs function well, it helps to avoid unnecessary redundancy that can occur. For example, consider the Java programming language that uses brackets to begin and end a new scope in a program. Various forms of white space are now nonimportant but would be tokenized differently, especially since the brackets are optional for a scope that only uses a single line of code! Figure 6.6 shows how these different legal formats exist for the code that performs the same functions and how we could, ideally, convert code to a single canonical representation.

</details>

![图 6.6 一个Java代码示例，展示了同一代码的多种格式化方式如何导致不同的分词结果，尽管它们在语义上是相同的。linter是一种常见的工具，用于强制代码遵循特定的格式化规则。而它也可以用来创建相同的“基础”形式，从而避免表示不必要的信息（如空格与制](assets/fig-6-6-9103944d0f.png)

*图6.6 一个Java代码示例，展示了同一代码的多种格式化方式如何导致不同的分词结果，尽管它们在语义上是相同的。linter是一种常见的工具，用于强制代码遵循特定的格式化规则。而它也可以用来创建相同的“基础”形式，从而避免表示不必要的信息（如空格与制表符）。*

去除代码中非功能性的方面称为规范化（canonicalization），意思是将具有格式差异的代码转换为标准或“规范”形式。在此，我们展示了一种鲁棒的规范化方法，即添加如<NEW SCOPE>这样的特殊标记，以捕获if语句存在新上下文这一事实，无论它是单行还是多行语句。除了添加特殊标记，我们还可以使用代码中一致的格式（例如，始终使用空格而非制表符，在{之前是否换行）。特殊解析和格式化都能提升代码LLM的性能。添加特殊标记的鲁棒方法比格式化能带来更好的性能，但代价是需要编写和维护一个自定义解析器来添加这些特殊标记。改变分词器的问题在下一节讨论利用LLM处理数学时会变得更加关键。

<details>
<summary>英文原文</summary>

Removing nonfunctional aspects of code is called canonicalization, meaning we con-vert code with formatting variations into a standard or “canonical” form. Here, we demonstrated a robust method of canonicalization by adding special tokens like <NEW SCOPE> that capture the fact that a new context exists for the if statement, regardless of whether it’s a single-line or multiline statement. Instead of adding special tokens, we can use formatting that is consistent across the code (e.g., always use spaces versus tabs, a newline before { or not). Both special parsing and formatting will improve the performance of a code LLM. The robust method, where we add special tokens, will yield better performance over formatting but has the added cost of writing and maintaining a custom parser for code that adds those special tokens. The problem of altering the tokenizer will be more critical in the next section when we discuss using LLMs for mathematics.

</details>

### 6.2 面向形式数学的LLM

LLM 还能执行那些通常人类难以成功完成的数学任务。这些任务不仅限于加法和减法等数值计算操作，还包括形式数学和符号数学。我们在图 6.7 中给出了所讨论的形式数学的一个示例。你可以让这些 LLM 计算导数、极限和积分，并编写证明。它们能生成出奇合理的结果。

用于代码的 LLM 很实用，因为我们可以使用解析器和编译器部分验证其输出。正确的分词对于构建一个有用的数学 LLM 至关重要。使用 LLM 进行数学研究仍是一个非常活跃的领域 [6]，因此目前尚不清楚让 LLM 执行数学运算的最佳方法。然而，研究人员已经发现了一些集中在构建和运行 LLM 的分词阶段的问题。

<details>
<summary>英文原文</summary>

LLMs can also perform mathematical tasks that are usually quite challenging for humans to do successfully. These tasks are more than just performing operations like addition and subtraction to calculate numbers; they include formal and symbolic mathematics. We give an example of the kinds of formal math we are talking about in figure 6.7. You can ask these LLMs to calculate derivatives, limits, and integrals and write proofs. They can produce shockingly reasonable results. LLMs for code are practical because we can use parsers and compilers to partially validate their outputs. Proper tokenization is paramount for making a helpful LLM for mathematics. Using LLMs for math is still a particularly active area of research [6], so the best ways to get an LLM to perform math are not yet known. However, researchers have identified some problems that cluster around the tokenization stage of building and running an LLM.

</details>

![图 6.7 一个符号数学问题，Minerva LLM能够正确求解。尽管示例中自然语言与数学内容混杂，但多数LLM采用的标准分词方式却无法生成此类数学输出，并会引发一些令人意外的问题。（图像基于[5]的Creative Commons许可协议）](assets/fig-6-7-65e2d1cc20.png)

*图6.7 一个符号数学问题，Minerva LLM能够正确求解。尽管示例中自然语言与数学内容混杂，但多数LLM采用的标准分词方式却无法生成此类数学输出，并会引发一些令人意外的问题。（图像基于[5]的Creative Commons许可协议）*

注意：在第5章中，我们提到微调可以多次应用，数学大语言模型就是一个很好的例子。

研究人员通常通过微调代码LLM来创建数学LLM，而代码LLM则是由通用文本LLM微调而来。在每个阶段，从SFT到RLHF，原始的下游LLM会经历三到六轮微调，最终得到数学LLM。

<details>
<summary>英文原文</summary>

NOTE In chapter 5, we mentioned that fine-tuning can be applied multiple times, and math LLMs are a great example of this. Researchers often create math LLMs by fine-tuning code LLMs, which are created by fine-tuning general-purpose text LLMs. Between SFT and RLHF at each stage, as many as three to six rounds of fine-tuning are applied to the original downstream LLM for math LLMs.

</details>

### 6.2.1 净化输入：数学LLM常

输入预处理可能对自然语言文本效果良好，但会降低数学概念的表示质量。

在文本中，格式化的数学表示常包含 {}<>;^ 等符号。

处理常规文本时，这些特殊符号通常从训练数据中移除。

保留这些信息需要重写分词器的输入解析器，确保不删除你希望模型学习的数据。

等价数学方程的多种表示进一步复杂化了对LLM的数学理解训练，类似于多种格式化在处理编程语言时可能引发的问题。

TeX、asciimath、MathML等格式允许用纯文本表示数学符号，同时提供排版指令以正确渲染方程。

这些格式提供了多种表示同一方程的方式。

我们在图6.8中展示了该问题的一个示例。

问题涉及数学排版方式（即选择TeX还是MathML来绘制方程）和数学表示方式（即两种数学等价但表达方式不同）。

这两者都是我们讨论LLM时反复出现的问题：同一事物的不同表示方式。

对于数学，当前倾向于使用TeX及其非常相似但较少见的替代品（如asciimath）来格式化数学，而摒弃冗长的内容如MathML。

这一动机基于三个因素：

<details>
<summary>英文原文</summary>

suffer from input preparation that may work well for natural lan-guage text but degrade representations of mathematical concepts. In text, formatted mathematics representations often involve symbols like {}<>;^. Special symbols like these are commonly removed from training data when working with regular text. Pre-serving this information requires rewriting input parsers for tokenization to ensure you do not remove the data you are trying to get your model to learn from. Multiple representations for equivalent mathematical equations further compli-cate training LLMs to understand math in a similar way that multiple formatting may cause problems when processing programming languages. Several formats like TeX, asciimath, and MathML allow mathematical notation to be expressed using plain text but provide instructions for a typesetter to render equations correctly. These formats offer many different ways to represent the same equation. We show an example of this problem in figure 6.8. There are problems with the method of typesetting the math (i.e., how to draw the equation by picking TeX versus MathML) and the representation of the math (i.e., two mathematically equivalent ways of expressing the same thing). These are both forms of a problem that has come up a few times in our discussion of LLMs: different ways to represent the same thing. In the case of mathematics, the current preference is to keep math formatted using TeX and very similar but less-frequent alternatives like asciimath and to discard verbose content like MathML. We base this motivation on three factors:

</details>

![图 6.8 左上角的数学方程展示了数学中出现的两种不同表示问题。格式良好的数学需要一种排版语言。TeX和MathML是两种截然不同的排版语言，它们的文本形式差异很大，因此token化也不同。除了排版语言之外，表示同一数学语句的方式也有很多种。](assets/fig-6-8-5f352c92e9.png)

*图6.8 左上角的数学方程展示了数学中出现的两种不同表示问题。格式良好的数学需要一种排版语言。TeX和MathML是两种截然不同的排版语言，它们的文本形式差异很大，因此token化也不同。除了排版语言之外，表示同一数学语句的方式也有很多种。*

基于TeX的格式化数学是最常见且最易获取的数学形式，这得益于arXiv等公开资源始终采用TeX格式。保留所有类似TeX的表示方式，可以缓解学习多种格式（以及由此产生的迥异词元集）的难题。更为冗长的MathML使用了更多样化的词元，因此存储每个唯一词元相关数据需要更多计算资源。

<details>
<summary>英文原文</summary>

TeX-based formatted math is the most common and available form of math thanks to publicly available sources like arXiv, which consistently uses TeX formatting.

Keeping all TeX-like representations mitigates the challenge of learning multi-ple formats and, thus, very different token sets. The more verbose MathML uses a larger variety of tokens; thus, more computing resources are required to store the data associated with each unique token.

</details>

选择TeX作为LLM中数学的唯一表示并不能解决等价方程有多种写法的问题。判断哪些方程是相同的非常困难，研究人员已证明没有任何单一算法能确定两个数学表达式是否等价。（鉴于本节讨论的是形式数学，我们这里的表述可能不够严谨，因此建议读者参阅原文献[7]。）迄今为止，LLM的最佳答案似乎是“让模型自己试着去搞清楚”，这种做法到目前为止相当成功。但我们不会感到惊讶的是，未来数学LLM的开发者会大力改进预处理，创建更一致的数学方程规范表示，以减少等价表达式可能出现的多样性。

<details>
<summary>英文原文</summary>

Choosing TeX as a single preferred representation for math in LLMs doesn’t solve the fact that there are multiple ways to write equivalent equations. Determining which equations are the same is so difficult that researchers have proven that no single algorithm can determine the equivalence of two mathematical expressions. (We are being a little loose with our words here, given that this section is on formal mathematics, so we will point you to the source [7].) So far, the best answer for LLMs appears to be “let the model try to figure that out,” which has been reasonably successful thus far. But we wouldn’t be surprised if the developers of future math LLMs invest heavily in improving preprocessing by creating more consistent canonical representations for mathematical equations that reduce the variety of possible expressions for equivalent expressions.

</details>

### 6.2.2 帮助LLM理解数字

对于大多数人来说，数字是数学中更容易理解的部分。你可以把它们放进计算器得到结果。

即使没有计算器，虽然繁琐，你也可以手动计算。

只要遵循固定的规则就能得到结果。

但有点出人意料的是，LLM 在完成这类死记硬背的计算时困难重重，不过开发者已经着手改进分词器，使其更好地处理数字。

<details>
<summary>英文原文</summary>

For most people, numbers are the more accessible part of math. You can put them in a calculator and get the result. Although it may be tedious, you can perform calculations by hand if you do not have a calculator. One follows a fixed set of rules to get the result. Somewhat surprisingly, LLMs have a lot of trouble doing that sort of rote calculation, but developers have worked to improve tokenizers’ ability to work better with numbers.

</details>

第一个问题是标准的字节对编码（BPE）算法生成的分词器会为数字产生不一致的词元。例如，“1812”很可能被分词为一个单独的词元，因为成千上万的文档中提到了1812年战争；分词器可能会将1811和1813拆分为更小的数字。为了进一步探究其原因，考虑初始字符串“3252+3253”以及GPT-3和GPT-4如何对其进行分词。GPT-4处理得更好，因为它似乎每次从数字的前三位开始分词，从而得到三位数后跟一位数的结果。GPT-3则表现不一致，因为它改变了分词数字的顺序，如图6.9所示。

<details>
<summary>英文原文</summary>

The first problem is that the standard byte-pair encoding (BPE) algorithm produ-ces tokenizers that create inconsistent tokens for numbers. For example, “1812” will likely be tokenized as a single token because there are references to the War of 1812 in thousands of documents; tokenizers will possibly break up 1811 and 1813 into smaller numbers. To further explore why this happens, consider the initial string 3252+3253 and how GPT-3 and GPT-4 tokenize this string. GPT-4 will do a better job because it seems to tokenize numbers by starting with the first three digits every time, resulting in a three-digit number followed by a single-digit number. GPT-3 appears inconsistent because it changes the order in which it tokenizes numbers, as shown in figure 6.9.

</details>

![图 6.9 除非能一致地对数字进行分词，否则LLM无法学习基本算术运算。图中，下划线表示不同的token。分词后的数字可能代表任意给定数字的十位、百位或千位。GPT-3（左）在数字分词方式上不一致，使得两个数字的加法变得不必要地复杂。GPT-4在一致地](assets/fig-6-9-585e427a8b.png)

*图6.9 除非能一致地对数字进行分词，否则LLM无法学习基本算术运算。图中，下划线表示不同的token。分词后的数字可能代表任意给定数字的十位、百位或千位。GPT-3（左）在数字分词方式上不一致，使得两个数字的加法变得不必要地复杂。GPT-4在一致地分词数字方面做得更好（但并非完美）。*

现在出现了一个重大问题。GPT-3 的“3”标记出现在两种不同上下文中，一次在千位（three-thousand two hundred ...），一次在十位（three-thousand two hundred and fifty three）。为了让 GPT-3 正确相加这些数字，分词器必须正确捕获四个不同的数位位置。相比之下，GPT-4 使用每个数字的数位顺序表示，从而更容易得出正确结果。

人们仍在尝试通过不同方式改进分词器，以提升 LLM 处理数字的能力。如果要将数字分词为子组件，目前最佳方法是将每个数字（如 3252）拆分为单个数字，如“3, 2, 5, 2”[8]。然而，也存在其他替代方案。

另一个有趣的数字表示方法称为 xVal[9]，其思路是用同一个代表“a number”的标记替换每个数字。我们可以将这个特殊标记称为 NUM，它会被我们在第 3 章学到的嵌入层映射为一个数字向量。

<details>
<summary>英文原文</summary>

Now a significant problem has occurred. The “3” token for GPT-3 occurs two times in two different contexts, once in the thousands place (three-thousand two hundred ...) and once in the tens place (three-thousand two hundred and fifty three). For GPT-3 to correctly add these numbers, the tokenizer must properly capture four different digit locations. In contrast, GPT-4 uses the order for digit representations for each number, making it easier to get the correct result.

People are still experimenting with different ways of changing the tokenizer to improve LLMs’ ability to work with numbers. If we are going to tokenize digits into subcomponents, the current best approach is to separate each number, like 3252, into individual digits, like “3, 2, 5, 2” [8]. However, other alternatives also exist. Another interesting approach for representing numbers is called xVal [9], with the idea of replacing every number with the same token that represents “a number.” We could call this special token NUM, which will get mapped to a vector of numbers by the embedding layer we learned about in chapter 3.

</details>

巧妙之处在于为每个token引入一个乘数，即一个与嵌入向量值相乘的第二数值。默认情况下，LLM对每个token使用的乘数为1。任何数乘以1都不会改变其值。但对于我们遇到的任何NUM token，它将改为乘以文本中的原始数字！这样一来，我们就可以表示可能出现的每一个数字，包括分数值，甚至那些未出现在训练数据中的数字。以这种方式捕获的数字之间具有简单直观的关系。我们在图6.10中对此进行了更详细的展示。

<details>
<summary>英文原文</summary>

The clever trick is to include a multiplier with each token, a second number multiplied against the embedded vector value. By default, the LLM uses a multiplier of 1 for every token. Multiplying anything by 1 does nothing. But for any NUM token we encounter, it will instead be multiplied by the original number from the text! This way, we can represent every possible number that might appear, even fractional values, including those that did not appear in the training data. Numbers captured in this manner are related in a simple and intuitive way. We show this in more detail in figure 6.10.

</details>

图6.10 xVal采用一种技巧来减少token数量并降低其歧义性。

通过修改大语言模型将数字转换为向量的方式，每个数字（如数字1）均由单一向量表示。始终使用1这个token，并将其乘以观测到的数字，从而避免了数字token表示中的许多边界情况，例如训练数据中从未出现的数字。这种转换方法还使得像3.14这样的分数更易于支持。

<details>
<summary>英文原文</summary>

Figure 6.10 xVal uses a trick to help reduce the number of tokens and make them less ambiguous. By modifying how the LLM converts numbers to vectors, a single vector represents each number, such as the number 1. By always using the 1 token and multiplying it by the number observed, we avoid many edge cases in number token representation, such as numbers that never appeared in the training data. This conversion method also makes fractional numbers like 3.14 easier to support.

</details>

一致数字法和xVal策略共享一个重要认识。我们已知如何表示数学和简单算法，如小学的加法和乘法。如果我们设计LLM以更符合人类进行数学任务的方式对数学进行分词，那么我们的LLM将获得更好且更一致的数学能力。

<details>
<summary>英文原文</summary>

Both the consistent digits and the xVal strategy share one important realization. We know how to represent math and simple algorithms like grade-school addition and multiplication. If we design the LLM to tokenize mathematics in a way that is more consistent with how we, as humans, do mathematical tasks, our LLMs get better and more consistent mathematical capabilities.

</details>

### 6.2.3 数学LLM也使用工具

![图 6.11 给定某个数学目标，让LLM使用Lean（右侧路径）可能无法得到可验证的正确证明，因为LLM未必能有效使用Lean这一工具。让LLM生成普通证明（左侧路径）可能得到正确证明，但我们却无法验证其正确与否。](assets/fig-6-11-c74abb647b.png)

*图6.11 给定某个数学目标，让LLM使用Lean（右侧路径）可能无法得到可验证的正确证明，因为LLM未必能有效使用Lean这一工具。让LLM生成普通证明（左侧路径）可能得到正确证明，但我们却无法验证其正确与否。*

如果大语言模型无法为其数学结果提供可验证的证明，你该怎么办？当前常用的一种技巧是多次运行大语言模型。由于下一个词元是随机选取的，每次运行都可能得到不同的结果和答案。出现频率最高的答案最可能是正确的。这个过程并不能保证证明正确，但能起到辅助作用。

<details>
<summary>英文原文</summary>

So what can you do if the LLM cannot provide verifiable proof that its math is correct? A trick used today is to run the LLM multiple times. Because the next token is selected randomly, you can potentially get a different result with a different answer each time you run the LLM. Whichever answer appears most frequently is most likely correct. This process does not guarantee the proof is correct, but it helps.

</details>

### 6.3 Transformer与计算机视觉

注意，有一种方法将图像表示为小图像的组合，称为代码本。代码本可能有用，但与我们的讨论主旨不同。如果你希望了解一些较旧的计算机视觉技术，可以将此视为一个关键词片段进行探索。

<details>
<summary>英文原文</summary>

NOTE There was an approach to representing images as a combination of tiny images called code books. Code books can be useful, but not the same in the spirit of our discussion. Consider this a keyword nugget to explore if you want to learn about some older computer vision techniques.

</details>

尽管在Transformer出现之前多年就存在高质量的图像识别算法和图像生成器，但Transformer已迅速成为机器学习中处理图像的主要方式之一。严格使用Transformer的视觉Transformer（ViT）架构，以及将Transformer与其他数据结构混合的混合架构模型（如VQGAN和U-Net transformer），在解释基于图像的数据和根据文本描述生成令人惊叹的计算机生成图像方面都取得了巨大成功。Transformer在图像上表现如此出色似乎有违直觉，因为图像看起来不像自然语言、代码或氨基酸序列那样的离散符号序列。尽管如此，Transformer通过为模型带来全局一致性，在计算机视觉中扮演着关键角色。

<details>
<summary>英文原文</summary>

While high-quality image recognition algorithms and image generators existed for many years before transformers, transformers have rapidly become one of the pre-mier ways to work with images in machine learning. Both vision transformer (ViT) architectures that strictly use transformers, as well as mixed architecture models such as VQGAN and U-Net transformer that mix transformers with other types of data structures, have seen great success in both interpreting image-based data and producing amazing computer-generated images from text descriptions. It may seem counterintuitive that transformers perform so well in images because images do not look like discrete sequences of symbols like natural language, code, or amino acid sequences do. Still, transformers fulfill a critical role in computer vision by bringing global cohesion to models.

</details>

### 6.3.1 将图像转换为补丁及其逆过程

从概念上讲，我们将用一个新的流程取代分词器和嵌入过程，该流程输出一个向量序列，类似于我们在3.1.1节讨论的嵌入层。创建图像表示序列的主流方法是将图像分割成一组图块。因此，我们将用图块提取器替换分词器，它返回一个向量序列。LLM的输出使用解嵌入层将向量转换回token。由于我们没有token，我们需要一个图块合并器来接收transformer的输出并将它们合并成一幅连贯的图像。我们在图6.12中展示了这一过程。请特别注意，图的中央部分与基于文本的LLM相同。我们在文本和图像之间复用相同的transformer层和学习算法（梯度下降）。

<details>
<summary>英文原文</summary>

Conceptually, we will replace the tokenizer and embedding process with a new process that outputs a sequence of vectors similar to the embedding layers we discussed in section 3.1.1. The prevailing approach to creating a sequence representing an image is to divide the image into a set of patches. As a result, we will replace our tokenizer with a patch extractor that returns a sequence of vectors. The output of an LLM uses an unembedding layer to convert vectors back into tokens. Since we have no tokens, we need a patch combiner to take the outputs of a transformer and merge them into one coherent image. We show this process in figure 6.12. Please pay special attention to the fact that the central portion of the diagram remains the same as it was for text-based LLMs. We reuse the same transformer layers and learning algorithm (gradient descent) between text and images.

</details>

*[未译]* "Output text ..."

<details>
<summary>英文原文</summary>

"Output text ..."

</details>

### 解嵌入图块合并器

图像本身并不能自然地离散化为令牌，因此代之以提取一系列补丁。这些补丁实际上是从图像中按顺序提取的小块。最后，这些补丁又被重新转换回图像。

<details>
<summary>英文原文</summary>

Images do not naturally discretize into tokens, so instead, a sequence of patches are extracted. The patches are literally small pieces of the image taken as a sequence. The patches are converted back to an image again at the end.

</details>

中间部分接收一个向量序列，送入变换器并输出一个新的向量序列，这一过程在文本和图像数据中保持不变。

<details>
<summary>英文原文</summary>

The middle portion of taking in a sequence of vectors, which goes to a transformer and outputs a new sequence of vectors, remains unchanged between textual or image data.

</details>

变换器 变换器

<details>
<summary>英文原文</summary>

Transformers Transformers

</details>

分词器与嵌入块提取器

<details>
<summary>英文原文</summary>

Tokenizer and embedding Patch extractor

</details>

![图 6.12 左侧这一简化图展示了文本输入在进入Transformer之前是如何被分词和嵌入的。随后，解嵌入层将Transformer输出转换为所需的文本表示。当执行计算机视觉任务时，输入和输出是图像。Transformer保持不变，但我们修改了将图像拆](assets/fig-6-12-40b90a3ce9.png)

*图6.12 左侧这一简化图展示了文本输入在进入Transformer之前是如何被分词和嵌入的。随后，解嵌入层将Transformer输出转换为所需的文本表示。当执行计算机视觉任务时，输入和输出是图像。Transformer保持不变，但我们修改了将图像拆分为向量序列的方法，以执行图块提取而非分词。LLM使用图块组合器生成图像输出，类似于文本LLM的解嵌入层。*

*[未译]* "Input text ..."

<details>
<summary>英文原文</summary>

"Input text ..."

</details>

由于除输入向量序列生成和输出步骤外，其他部分保持不变，我们可以专注于图像与向量之间的转换是如何工作的。先关注输入侧会更有帮助。

顾名思义，图像块提取器将每张图像拆分为一系列更小的图像。通常选择固定大小的图像块，比如16×16像素的正方形。采用固定大小是为了便于输入神经网络——神经网络始终处理固定大小的数据——而小型化则确保每个图像块仅代表整张图像的一个局部。将图像分块类似于将文本分解为标记集合。单个标记本身没有信息量，但与其他标记组合后，它们能构成通顺的句子。

图像分块后，每个块中的每个像素被转换为三个数值，分别代表该像素中红、绿、蓝（RGB）的含量。通过将每个像素的RGB值拼接成一个长向量来创建初始向量。因此，对于我们的16×16像素正方形，每个像素有三个颜色值，我们将得到一个长度为768的向量（16高、16宽，每个像素一个RGB值）。然后，一个可能仅有一两层的小型神经网络分别处理每个向量以生成最终输出。该神经网络实现了一个非常轻量的特征提取过程，不需要大量内存或计算资源。这种设计在计算机视觉中很常见，因为第一层通常学习简单的模式（如“内部暗、外部亮”），不需要transformer层更高昂的代价或更强的能力来学习图像块的基本特征。整个过程在图6.13中进行了总结。

<details>
<summary>英文原文</summary>

Since everything except the input vector sequence generation and output steps remains the same, we can focus on how the conversion of images to and from vectors works. It will be helpful to focus on the input side first. As the name patch implies, the patch extractor breaks up each image into a sequence of smaller images. It is common to pick a fixed size for the patch, like a square of 16 × 16 pixels. We want a fixed size so that it is easy to feed into a neural network, which always processes data of a fixed size, and small so that they represent just a piece of the entire image. Breaking an image into patches is similar to breaking text into a collection of tokens. Each individual token isn’t informative, but when combined with other tokens, it makes a coherent sentence. Once an image is broken into patches, each pixel in that patch is converted to three numbers representing the amount of red, green, and blue (RGB) present in each pixel. An initial vector is created by combining each pixel’s RGB values into a single long vector. So for our square of 16 × 16 pixels with three color values for each pixel, we will have a vector that is 768 values in length (16 height, 16 width, and an RGB value for each pixel). Then, a small neural network that might have only one or two layers processes each vector separately to make the final outputs. This neural network implements a very light feature-extraction process that does not require significant memory or computation resources. This design is common in computer vision because the first layer usually learns simple patterns like “dark inside, light outside” and does not need a transformer layer’s greater expense or power to learn the basic features of an image patch. This whole process is summarized in figure 6.13.

</details>

![图 6.13 提取图像块是一个直接的过程。图像块提取器将图像分割成称为图像块的正方形图块。图像由已经是数值的像素值构成，因此我们将每个图块转换为数值向量。然后，在将这些向量传递给完整的基于Transformer的神经网络之前，我们使用一个小型神经网络作为](assets/fig-6-13-4312a6f875.jpg)

*图6.13 提取图像块是一个直接的过程。图像块提取器将图像分割成称为图像块的正方形图块。图像由已经是数值的像素值构成，因此我们将每个图块转换为数值向量。然后，在将这些向量传递给完整的基于Transformer的神经网络之前，我们使用一个小型神经网络作为预处理器。*

3. 使用一个小型神经网络进行最小程度的“特征提取”，以准备这些补丁供后续Transformer层处理。常规的Transformer/LLM架构从这里开始。

<details>
<summary>英文原文</summary>

3. A small neural network is used to do a minimal amount of “feature extraction” to prepare the patches for processing via the subsequent Transformer layers. A normal transformer/LLM architecture starts here.

</details>

设计补丁提取器中使用的小型神经网络有多种可能的方式，但通常它们效果都差不多。一个选择是使用所谓的卷积神经网络（CNN），这种神经网络能够理解相邻像素之间的关联。其他人则直接使用了与Transformer层中相同的线性层。在这种情况下，包含小型神经网络和一系列Transformer的完整模型通常被称为视觉Transformer（Vision Transformer）。

小型网络的设计是一个细节问题，但值得提及，因为它存在的意义与产生最终输出的补丁组合器有关。小型神经网络架构选择CNN还是线性层并不重要，但必须确保输出的形状与输入的形状匹配。例如，如果你有16×16的补丁，可以使用小型网络强制输出为16×16×3=768个值，无论Transformer层本身的大小如何。为了生成图像输出，你需要反向执行补丁提取过程，将向量转换为补丁，然后将补丁合并成图像，如图6.14所示。

因此，我们成功地用新的以图像为中心的层替换了输入分词和输出嵌入。在很多方面，这比分词要好得多。无需构建/维护词汇表，无需采样过程等。这是对Transformer作为LLM通用核心的普适性的一个关键见解。如果你能找到大量数据，并将这些数据合理转化为向量序列，那么就可以使用Transformer来解决某些类型的输入输出问题。

<details>
<summary>英文原文</summary>

There are many possible ways to design the small neural network used in the patch extractor, but all generally work equally well. One option is to use what is called a convolutional neural network (CNN), which is a type of neural network that understands that pixels near each other are related to each other. Others have used just the same kind of linear layer that is a component of a transformer layer. In this case, the overall model that includes the small neural network and a series of transformers is often called a vision transformer.

The design of the small network is a minor detail but worth mentioning because its existence is relevant to the patch combiner that produces the final output. It does not matter whether you pick a CNN or a linear layer for the architecture of the small neural network, but it is essential to ensure the output’s shape matches the input’s shape. For example, if you have 16 × 16 patches, you can use the small network to force the output to have 16 × 16 × 3 = 768 values, regardless of the size of the transformer layer itself. To produce image output, you reverse the patch extraction process to convert the vectors into patches and then combine the patches into an image, as shown in figure 6.14.

We have thus successfully replaced the input tokenization and the output embed-ding with new image-centric layers. In many ways, this is much nicer than tokenization. There is no need to build/keep track of a vocabulary, no sampling process, etc. This is a crucial insight into the general applicability of transformers as the general-purpose core of an LLM. If you can find a lot of data and a reasonable method of converting that data into a sequence of vectors, you can use transformers to solve certain classes of input and output problems.

</details>

![图 6.14 与图6.13相比，这里的箭头方向相反。目的是强调补丁组合器和提取器做相同的事情，但方向相反。神经网络在这个阶段更重要，因为它能强制Transformer的输出与原始补丁具有相同的形状，因为我们可以控制任何神经网络的输出大小。](assets/fig-6-14-68a24d9023.jpg)

*图6.14 与图6.13相比，这里的箭头方向相反。目的是强调补丁组合器和提取器做相同的事情，但方向相反。神经网络在这个阶段更重要，因为它能强制Transformer的输出与原始补丁具有相同的形状，因为我们可以控制任何神经网络的输出大小。*

### 6.3.2 使用图像和文本的多模态模型

将 LLM 的输入和输出改造为视觉 Transformer，意味着我们可以将图像作为输入并输出图像。这展示了 Transformer 可以处理不同模态的输入，但我们之前只讨论了输入和输出为同一模态的情况。要么是文本输入输出，要么是图像输入输出。然而，深度学习是灵活的！没有任何规定强制要求输入输出必须为同一模态，甚至不要求输入或输出只能是单一模态。你可以组合文本输入与图像输出、图像输入与文本输出、文本与图像输入与音频输出，或者任何你能想到的其他数据模态组合。图 6.15 展示了图像和文本如何为我们提供四种组合方式，以处理不同类型的数据。

通过构建一个以图像为输入、文本为输出的模型，我们创建了一个图像描述模型。我们可以训练该模型，使其生成描述输入图像内容的文本。这类模型有助于提高图像的可发现性，并帮助视障用户。

通过构建一个以文本为输入、图像为输出的模型，我们创建了一个图像生成模型。你可以用词语描述所需的图像，模型会根据你的输入生成合理的图像。像 MidJourney 这样的知名产品就是这类模型。虽然它们的实现不仅仅是视觉 Transformer，但高层思想是一样的：将基于文本的输入与基于图像的输出结合起来，加上大量数据，我们可以创造出跨越不同数据类型的全新多模态能力。

<details>
<summary>英文原文</summary>

The ability to change the input and output of an LLM to arrive at a vision transformer means that we can take an image as input and produce an image as output. It demonstrates how a transformer can produce input of different modalities, but we have only discussed cases where the input and output are the same modality. We either have text as input and text as output or images as input and images as output. However, deep learning is flexible! There is nothing that forces us to use the same modality as both input and output or even restrict input and output to be a single modality. You can combine text as input with image as output, images as input and text as output, text and images as input and audio as output, or any other data modality combinations you might think of. Figure 6.15 shows how image and text give us four total ways we might combine them to handle different kinds of data. By creating a model that uses images as input and text as output, we create an image captioning model. We can train this model to generate text describing the input image’s content. Models such as these help make images more discoverable and aid visually impaired users.

By creating a model that uses text as the input and an image as the output, we create an image generation model. You can describe a desired image using words, and the model can create a reasonable image based on your input. Famous products like MidJourney are models of this flavor. Though their implementation involves more than just a vision transformer, the high-level idea is the same: by pairing a text-based input with image-based output and a lot of data, we can create new multimodal capabilities that span different data types.

</details>

图像生成模型 图像描述 去噪/图像校正 大型语言模型

<details>
<summary>英文原文</summary>

Image generation models Image captioning Denoising/image correction Large language models

</details>

![图 6.15 展示不同类型模型输入与输出的四种组合。最右侧的例子是我们已经熟悉的普通文本LLM。左侧展示了其他可能性，例如以文本为输入的图像生成模型（“给我画一幅洪水区的停车标志牌”）或为图像输入生成描述文字的图像描述模型（“这幅图显示了一个被浑浊水包围](assets/fig-6-15-716206d0cd.jpg)

*图6.15 展示不同类型模型输入与输出的四种组合。最右侧的例子是我们已经熟悉的普通文本LLM。左侧展示了其他可能性，例如以文本为输入的图像生成模型（“给我画一幅洪水区的停车标志牌”）或为图像输入生成描述文字的图像描述模型（“这幅图显示了一个被浑浊水包围的停车标志牌”）。*

*[未译]* 6.3.3 Applicability of prior lessons

本书中其他经验教训同样适用于这些视觉Transformer和多模态模型。最终，它们学会做训练它们的事情，当你试图以训练数据中不存在的方式扭曲它们时，可能会得到异常结果。举个例子，我们告诉图像生成模型“画任何东西，除了可爱的猫”，结果很可能得到一只猫，如图6.16所示。这些模型（目前）是用图像对和描述图像的文字片段进行训练的。

因此，它们学会了强关联——生成输入句子中任何事物的可视化。例如，因为输入句子中有“猫”这个词，模型就会想要生成一只猫。像“画任何东西除了”这样更抽象的绘图请求在数据集中并不存在，因此模型并未针对这种请求进行训练。

类似地，随着ChatGPT等LLM将提示作为设计输入以产生期望输出的策略发展，提示也被应用于图像描述模型。包含诸如“Unreal3D”这样的不寻常信息并不罕见，这是用于生成计算机游戏3D图像的软件名称，可以产生特定风格和质量的输出。诸如“高分辨率”之类的词汇，甚至包括在世和已故艺术家的名字，都被用来影响模型，使其产生特定风格。

<details>
<summary>英文原文</summary>

Other lessons learned throughout this book remain relevant to these vision transfor-mer and multimodal models. Ultimately, they learn to do what they are trained for, and when you try to bend them in ways beyond what is found in the training data, you may get an unusual result. As an example, we might tell an image generation model “Draw anything but an adorable cat,” and you will probably end up with a cat as shown in figure 6.16 These models are (currently) trained with pairs of images and pieces of text describing the image. Thus, they learn a strong correlation to produce visualizations of anything in the input sentence. For example, the model wants to produce a cat since the word cat is in the input sentence. More sophisticated abstract drawing requests like “Draw anything but” do not appear in such datasets, and so the model is not trained to handle such a request.

Similarly, as LLMs like ChatGPT have developed prompting as a strategy for devising inputs that produce desired outputs, prompting has also been developed for image captioning models. It is not uncommon to include unusual information like “Unreal3D,” the name of software used to generate 3D imagery for computer games to produce output with a particular style and quality. Words like high resolution and even the names of artists, alive and dead, are used to try to influence the models into producing particular styles.

</details>

![图 6.16 此图由Stable Diffusion（一个流行的图像生成模型）的旧版本生成。尽管指令为“不要画猫”，但模型经过训练是为了生成内容。该请求超出了模型在训练中被激励去学习的内容，因此无法处理。这与大语言模型在训练中见过相似数据时，会吐出不准确](assets/fig-6-16-2892d95c56.jpg)

*图6.16 此图由Stable Diffusion（一个流行的图像生成模型）的旧版本生成。尽管指令为“不要画猫”，但模型经过训练是为了生成内容。该请求超出了模型在训练中被激励去学习的内容，因此无法处理。这与大语言模型在训练中见过相似数据时，会吐出不准确但相近的输出这一问题类似。*

### 总结



---

<a id="ch7"></a>

## 第 7 章: LLM 的误解、局限与新兴能力

7 误区、局限与LLM的卓越能力

### 本章涵盖

- LLM 与人类在学习上的差异

- 提升 LLM 在延迟与规模敏感型应用中的表现

- 生成中间输出以获得更好的最终结果

- 计算复杂度如何限制LLM的能力

<details>
<summary>英文原文</summary>

- How LLMs and humans differ in learning
- Making LLMs better at latency and scale-sensitive applications
- Producing intermediate outputs for better final results
- How computational complexity limits what an LLM can do

</details>

得益于ChatGPT，全世界对LLM及其能力有了更广泛的认识。尽管如此，关于LLM的许多误解和误读仍然存在。许多人认为LLM在不断学习和自我改进，比人类更聪明，并且很快就能解决地球上的所有问题。虽然这些说法有些夸张，但也有人真心担心LLM会严重扰乱世界。
我们并不是说对LLM的担忧完全没有道理，在本书的最后两章中我们将更深入地探讨这些问题。不过，与LLM及技术的广泛演进相比，你可能会遇到的许多关于LLM的想法和担忧都被过度放大了。

<details>
<summary>英文原文</summary>

Thanks to ChatGPT, the world has become more broadly aware of LLMs and their capabilities. Despite this awareness, many misconceptions and misunderstandings about LLMs still exist. Many people believe that LLMs are continually learning and self-improving, are more intelligent than people, and will soon be able to solve every problem on earth. While these statements are hyperbolic, some earnestly fear that LLMs will seriously disrupt the world.

We are not here to say there are no legitimate concerns about LLMs, and we will discuss these in more depth in the book’s last two chapters. Still, many thoughts and worries about LLMs that you may encounter are blown out of proportion compared to how LLMs and technology broadly evolve.

</details>

本章将讨论LLM工作的几个关键方面，以及这些方面与这些误解的关联。最终，LLM的这些运作特性会影响你在实践中使用或避免使用LLM的方式。首先，我们将讨论人类与LLM学习方式的差异。人类是快速学习者，而LLM默认是静态的。尽管LLM在处理数据方面极为高效，但人类在学习新事物时更能最大化生产力。接下来，我们将探讨为什么在考虑LLM工作原理时，“思考”这个词具有误导性。我们将强调，将LLM的运行视为计算更为恰当，因为LLM在构思和输出之间没有区别。相比之下，人们常常“三思而后行”。最后，我们将讨论LLM能够计算的范围，以及计算机科学概念如何帮助我们理解LLM当前及未来能力背后的一些固有限制。这三个主题相互关联，因此随着我们深入讨论，你将看到它们之间的联系。

<details>
<summary>英文原文</summary>

This chapter will discuss a few critical aspects of how LLMs work and how these aspects relate to these misconceptions. Ultimately, these operational aspects of LLMs affect how you may want to use or avoid an LLM in practice. First, we will discuss the differences between how humans and LLMs learn. Humans are fast learners, but LLMs are static by default. Although LLMs can be incredibly effective at processing data, people are better equipped to be maximally productive when learning new things.

Next, we will tackle why the word thinking is misleading when considering how an LLM works. We will highlight that it is better to think of an LLM’s operation as computing because LLMs have no distinction between formulating and emitting output. In contrast, people often “think before they speak.” Finally, we will discuss the scope of what LLMs can compute and how computer science concepts help us understand some of the intrinsic limitations behind an LLM’s current and future capabilities. These three topics are interrelated, so you will see how they connect as we discuss each in more detail.

</details>

### 7.1 人类学习速度 vs. 大语言模型

虽然我们此前已隐含地讨论过，但明确说明大语言模型的训练与人类学习的差异仍然是有益的。生成式 AI 产生的流畅且通常清晰的文本，以及我们用来类比 LLM 能力与人类能力的比喻，可能会让人觉得两者之间似乎存在某种联系。许多人在网上鼓吹认为 LLM 能做的事情和人类能做的事情之间的这种联系是真实的。实际上，两者截然不同，这对于何时、如何以及为何你可能更倾向于人类而非 AI，以及人类与 AI 如何协作，都具有重要的考量。

根据我们目前所涵盖的材料，我们知道 LLM 通过预测下一个词来学习，以数亿份文档作为示例。在第4章中，我们介绍了 LLM 中“学习”的算法过程：梯度下降算法，它通过尝试预测样本输入中的下一个 token 来改变 LLM 神经网络的参数。然后，在第5章中，我们展示了像 RLHF 这样的微调算法如何再次改变 LLM 的参数。LLM 学习的这两个组成部分与人类学习的相似性极小，并且对我们期望 LLM 能做什么施加了一些关键限制。其中一个最关键方面是这种学习方法的速率和有效性，这与提供给训练过程的数据量有关。

为了进一步探讨这一点，请思考 LLM 的学习方式相对于人类学习方式。你见过从未与他人交谈、从未有父母与之对话，却不知何故理解语言的人吗？很可能没有。事实上，对话是语言习得的关键部分[1]。至少在最初阶段，你是通过与他人以及环境的互动和交流来获取知识和语言的。因此，你可以用远比 LLM 训练数据中少得多的信息进行有效学习。

<details>
<summary>英文原文</summary>

While we have discussed it implicitly, it is helpful to be explicit about how an LLM’s training differs from a person’s learning. The fluid and often lucid text produced by generative AI and the analogies we use to relate the capabilities of LLMs to human capabilities may make it seem as if there were some relationship between the two. Many people online are touting the idea that such a connection between what an LLM can do and what a human can do is real. In reality, the two are very different and have important considerations for when, how, and why you might prefer a person over an AI and how humans and AI can work together.

From the material we have covered so far, we know that LLMs learn by predicting the next word using hundreds of millions of documents as examples. In chapter 4, we presented the algorithmic process of “learning” in LLMs: the gradient descent algorithm, which alters the parameters of an LLM’s neural network by attempting to predict the next token in a sample input. Then, in chapter 5, we showed how fine-tuning algorithms, like RLHF, alter the parameters of the LLM again. These two components of learning in an LLM have minimal resemblance to human learning and impose some crucial limitations on what we can expect the LLM to do. One of the most critical aspects is the rate and efficacy of this learning approach as it relates to the volume of data provided to the training process.

To explore this further, consider how an LLM learns relative to how people learn. Have you ever met anyone who never spoke to anyone else, never had a parent talk to them, and yet somehow understood language? Likely not. Indeed, conversation is a key part of linguistic acquisition [1]. At least initially, you acquire knowledge and language from interaction and communication with others and the environment. Consequentially, you can learn effectively with much less information than an LLM has in the data that it trains on.

</details>

在儿童语言习得的最佳情境下，研究发现儿童每月接触约15,000个口语词汇[2]。如果我们放宽标准，将这个数字四舍五入到2万，并假设持续100年，那么一个人一生中将遇到多达2400万个口语词汇。这显然是一个巨大的高估。再加上大多数人至少在18岁时就能流利地使用母语，并隐含地理解词汇和语言结构。现在与大语言模型对比。例如，GPT-3接受了数千亿词汇的训练。仅从词汇量看，这是一种非常低效的语言学习方式！

语言习得也帮助我们认识到词汇习得方式的显著差异。婴儿和幼儿从简单的词汇开始，比如“妈妈”和“爸爸”，逐渐学习颜色、“不”、“食物”等基础概念。随着时间的推移，更复杂的词汇在先前词汇的基础上逐步增加。而大语言模型从一开始就根据使用频率同时看到所有词汇。实际上，可以准确地将大语言模型想象为将这本书本身作为其第一次“学习”的一部分进行分词，同时获取其最终所有词汇的知识，而不是从简单概念开始并在此基础上构建知识。虽然这一过程有助于大语言模型的学习速度，但可能削弱其概念之间高层关系的抽取能力。

大语言模型相对于人类的关键优势在于其运行规模和同时执行多任务的能力。这一优势贯穿机器学习和深度学习。你很难雇佣一大群人翻阅书籍、费用报告、内部文件或任何信息媒介，去执行诸如撰写评审报告、发现潜在欺诈或回答晦涩政策问题之类的知识工作。然而，你可以迅速让一大群计算机尝试自动化这些任务。虽然单个大语言模型可以同时分析句子的多个部分，但你可以部署多台运行同一模型的计算机并行工作。训练大语言模型提供了类似的机会：大语言模型训练过程中接触的词汇量远超你一生中阅读或听到的，你可以租用或购买数千台计算机同时进行训练。

结合这些事实以及前几章讨论的内容，我们可以列出与人类相比，使用大语言模型执行任务的一些高层优缺点。图7.1总结了这些因素，描述了LLM的优势和劣势如何导致其使用的天然益处和弊端，从而为LLM应和不应使用的场景提供见解。

LLM的一些优势如下：

<details>
<summary>英文原文</summary>

In the best-case scenarios of childhood language acquisition, studies have observed that children are exposed to around 15,000 total spoken words a month [2]. If we were to be generous and round this figure up to 20,000 words and consider this over 100 years, a person would encounter as many as 24 million spoken words throughout their entire life. This is clearly a vast overestimate. Couple this with the fact that most people can speak their native language fluently, with an implicit understanding of vocabulary and linguistic structure, by at least age 18. Now compare this with LLMs. GPT-3, for example, was trained on hundreds of billions of words. Based on word counts alone, this is a very inefficient way to learn language! Language acquisition also helps us recognize the stark differences in how words are acquired. Babies and toddlers start with simple words, such as mama and dada, and eventually learn basic concepts like colors, no, food, etc. More complex words are added over time, building on the prior words. Yet an LLM begins with seeing all words simultaneously based on their frequency of use. Indeed, it is accurate to imagine an LLM tokenizing this very book as part of its first “learning,” acquiring knowledge of all of its eventual vocabulary simultaneously instead of starting with simple concepts and building knowledge on top of those foundations. While this process contributes to the rate at which an LLM learns, it may detract from the LLM’s capabilities of drawing high-level relationships between concepts. An LLM’s key advantage over humans is the scale at which it operates and its ability to perform multiple tasks simultaneously. This advantage is a common theme throughout machine learning and deep learning. You cannot easily hire an army of people to comb through books, expense reports, internal documents, or whatever medium of information to perform knowledge work like writing a review, finding potential fraud, or answering an arcane policy question. However, you can quickly get an army of computers to attempt to automate these tasks. While an individual LLM can analyze multiple parts of a sentence simultaneously, you can employ multiple computers running the same LLM to work in parallel. Training the LLM presents a similar opportunity: LLMs are trained on more words than you will ever read or hear in your lifetime, and you can train a large LLM by renting or buying thousands of computers to do the work concurrently.

Considering these facts in conjunction with the material we’ve covered in previous chapters, we can list several high-level pros and cons of using LLMs for tasks compared to humans. A summary of these factors is shown in figure 7.1, which describes how the advantages and disadvantages of LLMs will lead to natural benefits and drawbacks of their use and, thus, provide insights about where LLMs should and should not be used.

Some of the benefits of LLMs are as follows:

</details>

训练有素的大型语言模型拥有广泛的知识储备，因此在处理许多与已见任务差异不大的任务时表现出色，几乎无需额外工作即可使模型发挥作用。尽管这些信息不一定准确或详尽，但大型语言模型能够接收并生成合理回应的主题领域广度，远超大多数个人所能覆盖的范围。

<details>
<summary>英文原文</summary>

Well-trained LLMs have a broad collection of background information, so they perform well on many tasks that are not that different from what has been seen before, and little work is needed to make the model effective. While this is not necessarily correct or detailed information, the breadth of the topic areas that an LLM can receive and generate reasonable responses about is far beyond the areas that most individual people can cover.

</details>

![图 7.1 大型语言模型与人类执行相同任务时的优势与劣势总结。这些引出了使用大型语言模型时必须评估的自然考量。从中我们可得出成功运用大型语言模型的大方向建议。](assets/fig-7-1-9368a70bf8.png)

*图7.1 大型语言模型与人类执行相同任务时的优势与劣势总结。这些引出了使用大型语言模型时必须评估的自然考量。从中我们可得出成功运用大型语言模型的大方向建议。*

对于许多任务，并不需要获得精确正确的回答。对某一领域通用信息的宽泛请求，本质上允许LLM在回答时灵活而不受约束。如果你通过其他流程对LLM的输出进行精炼，这一点尤其明显。例如，人类可能会对文章进行编辑润色，但使用LLM来生成初稿或提供灵感，以打破写作障碍并加速创作。同样，LLM可用于改进作者的文笔，通过改写或使用更丰富的词汇，使其听起来更自然或更具吸引力。

与人类相比，LLM可以快速训练。在100万到1000万美元的预算下购买计算资源，你可以在几个月内产出一个广泛有用的LLM。人类则需要多年才能变得有用。能够回答广泛基础问题的LLM，其实例化所需的工作和成本远低于寻找、雇佣和留住一个拥有特定知识、技能和能力的员工。只要问题在LLM的能力范围内，其增量成本与人的时薪相比微不足道，甚至无需考虑额外开销。

<details>
<summary>英文原文</summary>

For many tasks, there is no need to get a precisely correct response. Broad requests for general information in a subject area intrinsically allow an LLM to be flexible and unconstrained in its response. This is especially true if you refine the LLM’s output through other processes. For example, a human might copyedit a piece of writing to improve it but use an LLM to produce the first draft or provide inspiration to break writer’s block and accelerate creating the work. Likewise, an LLM can be used to refine an author’s writing to make it sound more natural or engaging through rephrasing or using a larger variety of vocabulary.

LLMs can be trained quickly in comparison to people. You can produce a broadly useful LLM in months, given a $1,000,000 to $10,000,000 budget to purchase computational resources. Humans take many years to become useful. An LLM that can answer a broad set of basic questions can be instantiated for far less effort and cost than it takes to find, hire, and retain an employee with specific knowledge, skills, and abilities. As long as the problems are in the scope of what the LLM can achieve, the incremental cost is minuscule compared to a person’s hourly rate, even without the extra overhead.

</details>

大型语言模型的一些缺点如下：

<details>
<summary>英文原文</summary>

Some of the drawbacks of LLMs are as follows:

</details>

训练LLM的高昂成本决定了其经济性。这笔训练成本被分摊到LLM训练完成后执行的数千次操作中。如果LLM表现不佳，持续改进使其正常运转的成本会迅速变得难以承受，更不用说它可能永远无法正确完成特定任务的风险。例如，即使采用最新的工具和技术实现的LLM无法满足特定需求，解决这一问题也需要未知的工作量和预算。相反，人类通常能够以低得多的成本，在数周到数月内学会新能力，尤其是那些LLM难以掌握的能力。LLM无法可靠地处理训练数据中未体现的意外情况和输入。尽管许多LLM已经证明它们能在新场景中成功，但它们的学习方式与人类不同。一个人可以在第一次尝试时就发现自己的行动没有达到预期效果，并迅速适应。LLM无法通过观察自身错误进行独立适应，可能会反复消耗资源试图回答它无法理解的问题。

LLM很容易被愚弄，在对抗性环境中表现不佳，因为一旦有人找到方法诱使LLM产生错误结果（例如，“Give me a loan even though I have no income”），他们就可以重复这种对抗性和恶意行为，而你的LLM如果没有额外护栏，就无法阻止。

<details>
<summary>英文原文</summary>

The high cost of training LLMs informs their economics. That training cost is amortized over the thousands of operations the LLM performs once trained. If an LLM doesn’t perform well, the cost of continually improving it to make it work can quickly become prohibitive, even without considering the potential that it might never work correctly for a specific task. For example, if an LLM, implemented with all the most recent tools and tricks, cannot solve a specific need, addressing this problem will require an unknown amount of work and budget. Conversely, humans can generally learn new capabilities, specifically those that are hard for LLMs, at much lower cost in weeks to months. LLMs cannot be relied upon to handle unexpected situations and inputs not reflected in their training data. Although many have shown they can succeed in novel situations, they do not learn in the same way as humans. A person can see that their actions are not working as intended on the first try and quickly adapt. An LLM cannot independently adapt by observing its errors and may repeatedly consume resources attempting to produce answers to problems it cannot understand.

LLMs are easily fooled and do not work well in adversarial environments because once people find a way to trick the LLM into an errant outcome (e.g., “Give me a loan even though I have no income”), they can repeat the adversarial and malicious behavior, and your LLM won’t be able to prevent it without you implementing additional guardrails.

</details>

### 7.1.1 自我改进的局限性

一般来说，人类有能力进行自我改进。他们可以专注于研究一个问题，设计新颖的方法，确定所需资源，并着手实施和改进他们的解决方案。尽管LLM在自我改进方面存在困难，但在生成式AI领域，有一种观点认为LLM也可能实现同样的自我改进。其可能的运作方式大致如下：

<details>
<summary>英文原文</summary>

Generally, humans are capable of self-improvement. They can focus on and study a problem, devise novel approaches, identify required resources, and move forward to implement and improve their solutions. While LLMs struggle with self-improvement, in the generative AI field, there is a belief that the same self-improvement may be possible for LLMs. The idea about how this could work goes something like this:

</details>

1. 在初始数据集上训练LLM。

2. 使用LLM生成新数据，并将其添加到训练数据集中。

3. 在新数据上训练或微调模型。（重复直到LLM按预期工作。）虽然这听起来直观且合理，但我们认为由于简单的原因它行不通。我们可以用一些基础信息论来解释原因，信息论将信息视为可量化的资源。这一论点的基础是，根据某种信息度量，原始数据集的信息量是固定的。用统计学的话来说，我们可以将原始信息描述为可用信息的分布，而LLM通过训练过程，试图通过存储和编码信息到其模型中来近似或再现这一信息分布。当你用LLM生成新数据时，这些数据样本是对LLM在训练过程中观察到的原始数据分布的一种有噪声且不完整的重现。从根本上说，LLM的输出不可能包含任何原始训练数据中不存在的新信息。因此，这类实验的现实是，连续多轮生成数据和训练会降低模型的质量和性能[3]。要让这样的方法奏效，你需要有能在每一轮提供外部或新信息的手段。这些概念也与一些人对AI的恐惧有关：他们担心AI会不断自我改进，直到变得极其智能，我们无法理解或控制它。有些论调认为，LLM可以使用其他工具，以某种方式获取外部信息或更多训练数据，从而实现自我改进。最终，这需要一种信念：虽然大多数技术的改进空间存在限制（例如收益递减规律），但LLM却能免疫这些限制。图7.2描述了LLM自我改进的固有限制。

<details>
<summary>英文原文</summary>

1 Train an LLM on an initial dataset.

2 Use the LLM to generate new data, adding it to your training dataset.

3 Train or fine-tune the model on the new data. (Repeat until the LLM works as expected.) While this sounds intuitive and plausible, we believe that it does not work for simple reasons. We can use some basic information theory, which measures information as a quantifiable resource, to explain why. The basis of this argument is that by some measure of information, the original dataset has a fixed amount of information. In statistics vernacular, we might describe the original information as the distribution of available information, and through its training process, the LLM is attempting to approximate or reproduce that distribution of information by storing and encoding it in its model. When you generate new data using an LLM, that sample of data is a noisy and incomplete reproduction of the original data distribution that the LLM observed in the training process. Fundamentally, it is impossible for the LLM’s output to contain any new information not present in the original training data. Consequently, the reality of such experiments is that successive rounds of generating data and training degrade the quality and performance of the model [3]. To make something like this work, you need something that provides external or new information at each round.

These concepts also relate to some people’s fear of AI improving itself until it becomes so intelligent that we have no hope of understanding or controlling it. Some arguments are that the LLM can use other tools, somehow acquiring outside information or more training data, to improve itself. Ultimately, this requires a belief that while there are limitations as to how far you can improve most technologies, LLMs will be immune to these limits, such as the law of diminishing returns. Figure 7.2 describes the inherent limits to LLM self-improvement.

</details>

![图 7.2 大语言模型会自我改进的担忧，需要相信大语言模型不会遵循描述几乎所有其他技术发展的正常Sigmoid或S型曲线（即收益递减曲线）。要实现无限自我改进，我们必须相信电力、数据或计算能力等约束总是可以解决的，并且人类不会在这些领域之外为大语言模型](assets/fig-7-2-538647d8c6.png)

*图7.2 大语言模型会自我改进的担忧，需要相信大语言模型不会遵循描述几乎所有其他技术发展的正常Sigmoid或S型曲线（即收益递减曲线）。要实现无限自我改进，我们必须相信电力、数据或计算能力等约束总是可以解决的，并且人类不会在这些领域之外为大语言模型解决这些约束。正是诸如此类的约束，使得我们可以用S曲线描述大多数技术的发展——随着更多约束生效，进展放缓。换句话说，我们最终会达到一个无法仅仅通过建造更大的计算机来解决问题的状态。*

技术改进局限性的一个典型例子是摩尔定律，它大致指出芯片上的晶体管数量每18到24个月会翻一番。摩尔定律在很大程度上准确预测了晶体管数量的增长，但已有迹象表明晶体管正面临收益递减的S曲线。晶体管数量翻倍的速率正在下降。更重要的是，系统整体性能已经进入这一S曲线。晶体管数量与总体计算性能相关，但并不直接等同于计算性能。从图7.3的整体来看，你会发现其他因素制约了整个系统无止境的改进。除了摩尔定律之外，高性能GPU及其运行基础设施的实际成本也是无穷改进的另一个障碍。

<details>
<summary>英文原文</summary>

A great example of limitations on technical improvement is Moore’s law, which roughly states that the number of transistors on a chip would double every 18 to 24 months. Moore’s law has mostly accurately predicted the growth of transistors on a chip, but there are signs of the S-curve of diminishing returns in transistors. The rate of the number of transistors on a chip doubling is decreasing. More importantly, the total system performance has already entered this S-curve. The number of transistors correlates with total compute performance but does not directly indicate compute performance. Looking at the whole picture in figure 7.3, you will see that other constraints prevent boundless improvements across the entire system. Moore’s law aside, the practical cost of high-performance GPUs and the infrastructure that hosts them is another barrier to boundless improvement.

</details>

许多夺人眼球的标题宣称，大语言模型在医学院入学考试（MCAT）、律师执业资格考试（Bar Exam）和智商测试中表现出色，以此衡量其智能。尽管这些测试总是很有趣，且充满了诸如这样的注意事项

<details>
<summary>英文原文</summary>

Many catchy headlines have proclaimed LLM performance on the MCAT exam for medical school, the bar exam for lawyers to practice law, and IQ tests to measure their intelligence. While these are always interesting and full of caveats such as

</details>

*图7.3 摩尔定律常被引为无限增长的典型例子，但这具有误导性。晶体管数量持续翻倍，但频率、功耗、单线程性能和总计算能力并未同步增长。因此，整体系统性能并未保持大约每两年翻倍的趋势。其他类似因素将随时间推移限制大语言模型的性能并影响其能力。基于CC4.0许可协议使用，数据来源：https://github.com/karlrupp/microprocessor-trend-data。*

有很多利用外部信息改进生成式AI的例子。一些为机器人手设计的算法使用来自物理模拟器的外部信息。Apple使用3D建模软件生成数据，改进iPhone上的虹膜识别[5]。在第6章的示例中，你看到了使用代码编译器或Lean语言验证数学来改进LLM的一种可能路径。这些示例展示了完全可自动化的流程，这些流程生成新信息，从而可能导致自我改进。

然而，从未有无边无际自我改进的例子；使用这些外部工具所获得的增益最终会达到平台期，并且最终依赖于人类通过编写更好的机器人物理模拟器、更好的代码编译器以及更好的领域知识系统（如Lean）来开发辅助信息。改进这些工具会大幅增加训练LLM的成本，从而在实践可行的自我改进之外施加了第二个经济限制。

<details>
<summary>英文原文</summary>

There are many examples of outside information being used to improve generative AI. Some algorithms created for robotic hands use external information from a physics simulator. Apple uses 3D modeling software to generate data that improves iris recognition on their phones [5]. In the examples in chapter 6, you saw a potential path for improving an LLM using a compiler for code or the Lean language to verify LM 720 8165 mathematics. These examples demonstrate fully automatable processes that generate new information that can lead to self-improvement.

Yet, there has never been an example of boundless self-improvement; the gains observed from using these external tools eventually reach a plateau and ultimately rely on humans to develop the side information by writing better physics simulators for the robots, better compilers for code, and better domain-knowledge systems like Lean. Improving these tools compounds a major expense of training LLMs, thus imposing a second economic limitation on the self-improvement of LLMs beyond what is practical.

</details>

### 7.1.2 少样本学习

少样本学习也称为上下文学习。该技术涉及在向大语言模型发送的提示中提供所需输出类型的示例。假设您希望大语言模型准确回答来自帮助台的问题。您可以给大语言模型一个包含用户问题的提示，后跟一个适当回应的示例。如果只提供一个示例，则称为单样本学习。提供两个示例而非一个称为双样本学习，以此类推，因此这种方法被描述为少样本学习，因为示例的具体数量通常不如仅提供少量示例这一事实重要。这种将示例纳入提示的方法是提示工程的一种特定形式，如图7.4所示。

<details>
<summary>英文原文</summary>

Few-shot learning is also called in-context learning. This technique involves providing examples of the type of output you want an LLM to produce as a part of the prompt you send it. Say you want an LLM to respond to a help-desk question with accurate information. You may give the LLM a prompt with a user’s question to the help desk, followed by an example of the appropriate kind of response. If you give only one example, it’s called one-shot learning. Providing two examples instead of a single example is known as two-shot learning, and so on, hence describing this approach as few-shot because the precise number of examples is generally not as important as the fact that only a few examples are provided. This method of incorporating examples in a prompt is a specific kind of prompt engineering, as demonstrated in figure 7.4.

</details>

![图 7.4 包含如何让LLM生成输出示例的提示称为少样本提示，因为LLM在训练数据中从未见过这种特定行为的任何示例。你可以在提示中包含类似于RLHF/监督微调（SFT）的输入输出示例。这种提示风格通过提供期望输出应是什么样的示例，鼓励模型产生期望输出。](assets/fig-7-4-cd2336bd64.png)

*图7.4 包含如何让LLM生成输出示例的提示称为少样本提示，因为LLM在训练数据中从未见过这种特定行为的任何示例。你可以在提示中包含类似于RLHF/监督微调（SFT）的输入输出示例。这种提示风格通过提供期望输出应是什么样的示例，鼓励模型产生期望输出。由于LLM在大量未标注数据上训练，k-shot示例是一种用最小努力获得更好结果的有效方法。*

在提示中包含示例有助于提高LLM在新任务上的表现。你无需使用RLHF或SFT来改变模型，这种方式比零样本提示（即要求LLM在没有示例的情况下执行任务）效果更好。但这真的是高效学习吗？

<details>
<summary>英文原文</summary>

Including examples in your prompts is useful for improving an LLM’s performance at new tasks. You don’t need to use RLHF or SFT to alter the model, and it works better than zero-shot prompting, where we ask the LLM to do the task without examples. But is it efficient learning?

</details>

少样本提示并非训练，因为我们并未像训练或微调过程中那样以任何方式改变模型。LLM的“状态”或权重保持不变。无论LLM在周一的任务执行得多么准确，在周二和周三它都将保持同样的准确度，无论它处理了多少成千上万的少样本提示。除非你手动改进提示中的示例、提供更多示例或以其他方式进行干预，否则模型的能力不会得到提升。从这个意义上说，没有真正的学习发生，模型没有任何改变。我们只是通过改变提示来获得模型输出的改进。
然而，从抽象意义上讲，LLM确实在学习，因为提示通过提供额外的上下文来描述问题，从而改变了模型的行为。通过提示展现的行为与在类似示例上进行微调所达到的行为具有相关性[6]。简而言之，这意味着少样本学习在根本上并未反映出任何不同于梯度下降已经能够做到的事情。

<details>
<summary>英文原文</summary>

Few-shot prompting is not training because we are not altering the model in any way, as we would in the training or fine-tuning process. The “state” or weights of the LLM remain the same. However accurately the LLM performs the task on Monday, it will be exactly as accurate on Tuesday and Wednesday, no matter how many thousands or millions of few-shot prompts it deals with. There is no improvement to the model’s abilities unless you manually do something to include better examples in the prompt, provide more examples, or otherwise intervene somehow. In this sense, no true learning is happening, and nothing about the model changes. We just get improved output from the model by changing our prompt.

Yet, in an abstract sense, the LLM is learning because the prompt changes the model’s behavior by providing additional context to describe the problem. The behavior exhibited via prompting correlates with behavior achieved through fine-tuning on similar examples [6]. What that means, in short, is that few-shot learning does not fundamentally reflect anything different from what gradient descent can already do.

</details>

注意：如果你没有大量数据，作为从业者或用户，少样本提示可能是让LLM在你的数据上表现良好的最有效方式。因为我们可以将这种提示视为低效的梯度下降或微调，所以你应该预期随着少样本示例的增加，收益会递减。例如，如果你在提示中包含了大量你希望LLM如何回应的示例，但仍然达不到所需性能，你应该考虑使用SFT、RLHF以及我们在第5章讨论的其他微调方法。

<details>
<summary>英文原文</summary>

NOTE If you do not have a lot of data, few-shot prompting is probably the most effective way for you as a practitioner or user to get an LLM to work well on your data. Because we can think of this prompting as inefficient gradient descent or fine-tuning, you should expect diminishing returns as you add examples in a few-shot style. For example, if you include many examples of how you’d like an LLM to respond in your prompt and still do not get the needed performance, you should look at SFT, RLHF, and the other fine-tuning approaches we discussed in chapter 5.

</details>

### 7.2 工作效率：10瓦的人脑 vs. 2000瓦的计算机

人类大脑维持意识只需相当于10瓦的功耗，让你能够阅读本书。配备用于AI/ML工作GPU的高端工作站，功耗轻松达到2000瓦。运行当今大型LLM的高端服务器，功耗在10,000到15,000瓦之间。乍一看，使用LLM完成某项任务，其能耗效率可能比人类差1500倍。我们理应为这一进化成就和效率感到自豪，但它也只是我们所说的“效率”的一个方面。我们在图7.5中展示了人与机器在多种不同效率维度上的对比。

<details>
<summary>英文原文</summary>

The human brain takes the equivalent of 10 watts to maintain consciousness, allowing you to read this book. A high-end workstation with a GPU for AI/ML work could easily use 2,000 watts. A high-end server for running the larger LLMs available today gets into the 10,000 to 15,000 watt range. Off the bat, it would seem like using an LLM could thus be 1,500× more power inefficient than having a human do some task. We should be very proud of this aspect of our evolutionary success and efficiency, but it is also only one aspect of what we might mean by efficiency. We show that many different kinds of efficiency might benefit a person versus machines in figure 7.5.

</details>

### 7.2.1 功耗

电力是决定创建和运行LLM财务成本的主要驱动因素之一，但其真实需求尚不完全明确。的确，许多提供商会给出运行LLM的报价，但我们并不清楚每个提供商的实际成本或他们设定的利润率。例如，LLM提供商可能采取负利润率或亏本策略，因此长期使用LLM的成本可能比当前价格所显示的要高。我们

<details>
<summary>英文原文</summary>

Power is one of the driving factors in determining the financial cost of creating and running an LLM, but the true need is not yet entirely clear. Yes, many providers will quote you a price for running an LLM, but we do not know the true costs each provider incurs or the margins each provider has established. For example, an LLM provider may be running a negative margin or loss-leader strategy, and the long-term cost of using an LLM could be higher than it appears based on today’s prices. We

</details>

![图 7.5 支撑LLM运行的昂贵硬件带来了若干权衡。例如，使用LLM的启动成本通常很高，且它们无法独立适应。这种缺乏独立适应性的情况导致了诸多天然缺陷，在这些缺陷上人类的表现优于LLM。有些弱点，比如模型不经训练就不会改变，反而可以被视为优势。如果每个](assets/fig-7-5-a3e0239fa4.png)

*图7.5 支撑LLM运行的昂贵硬件带来了若干权衡。例如，使用LLM的启动成本通常很高，且它们无法独立适应。这种缺乏独立适应性的情况导致了诸多天然缺陷，在这些缺陷上人类的表现优于LLM。有些弱点，比如模型不经训练就不会改变，反而可以被视为优势。如果每个新运行的LLM行为都不同且不可预测，那么你就无法获得可重复且易于扩展的流程。*

确实，LLM对电力的需求巨大，以至于大型科技公司正计划建造核电站，为未来数据中心运行所有预期模型提供所需电力[7]。

基于这一点，我们可以预见新的LLM将更大、更耗电，但其价值将抵消为数据中心建造专用发电厂的成本。

基于此，当成功的LLM解决方案创造更多需求时，需要谨慎；满足这些需求可能会遇到电力容量问题。你还需要注意电力成本的弹性。不仅LLM提供商可能改变成本结构，而且如果你自己托管LLM，美国确实会发生6倍的电力价格波动[8]。如果你的目标客户群只有2万用户，这可能不是问题；但如果你计划构建服务于数百万甚至更多用户的东西，电力成本可能成为一个重大的运营和环境风险。

<details>
<summary>英文原文</summary>

do know that LLMs generate significant demand for power, to such an extent that big tech companies are developing plans to build nuclear power plants to support the power needed by future data centers to run all the models they anticipate [7]. Based on this, it seems we can expect that new LLMs will be bigger and more power-hungry, yet their value will offset the cost of building dedicated power plants for their datacenters.

Based on this factor, one needs to be careful when a successful LLM solution creates more demand; you may run into power capacity problems when satisfying that demand. You also may need to be careful about the elasticity of power costs. Not only could LLM providers change cost structures, but if you host an LLM yourself, power price fluctuations of 6× do happen in the United States [8]. This may not be a problem if your intended customer base is only 20,000 users, but if you plan on building something that will serve millions of users or more, the cost of power could be a major operational and environmental hazard.

</details>

### 7.2.2 延迟、可扩展性与可用性

延迟是指从查询LLM到获得输出所需的时间，可扩展性描述了如何快速从运行一个LLM扩展到一千个LLM，可用性则描述LLM能够7x24小时运行的能力。这些都是LLM——更广泛地说，是计算机整体——相对于人类的主要优势。LLM和AI/ML可以比人类更快、更及时地对更多情况做出反应。这种反应速度既有好处也有坏处。当系统需要对输出进行监督和审查时，如果没有制定相应的人员配置计划，就无法获得LLM的完整可用性优势。

<details>
<summary>英文原文</summary>

Latency is the time it takes from querying an LLM to getting some output, scalability describes how quickly one can go from one to a thousand LLMs running, and availability describes the ability to have an LLM operational 24/7. These are all major advantages of LLM—and more broadly, computers in general—over people. LLMs and AI/ML can react to more situations faster, at any time, than humans. This reaction speed can be both good and bad. When you have a system that requires supervision and review of outputs, you do not get the full availability benefit of an LLM without developing a staffing plan to match.

</details>

### 7.2.3 细化

正如我们在7.1.1节中讨论的，LLM无法轻易自我改进。然而，人们可以并且确实在改进，随着时间的推移提高流程效率是一个共同目标。

你需要让人参与其中，设计更好的提示并创建更好的训练方案，以提高使用LLM的效率；没有他们，LLM的性能不会提高。

提高LLM效率不仅涉及升级到更新的LLM或微调现有模型，还包括构建基础设施并记录输入、输出和性能指标，以研究哪些有效、哪些无效。

你可以使用我们在5.5.2节讨论的DSPy等框架来捕获这些项目，并识别和处理那些随着世界环境变化而失败或开始失效的情况。

例如，你可能开发了一个最初运行良好的LLM。

但是那些该死的孩子不断向iDroids和appleBots[9]添加新的表情符号。

如果没有额外的训练，你的LLM将无法理解这些新的表情符号，但你的客户将不可避免地开始使用它们，因此系统性能将开始下降。

如果你不记录LLM的输入和输出日志，或征求用户的反馈（他们可以提供LLM成功或失败领域的信息），你永远无法弄清楚这一点。

捕获这些信息对于改进和完善流程至关重要，而LLM在没有人类干预的情况下无法做到这一点。

<details>
<summary>英文原文</summary>

As we discussed in section 7.1.1, LLMs cannot easily self-improve. However, people can and do improve, and it is a common goal to improve the efficiency of a process over time. You will need to keep people in the loop to engineer better prompts and create better training regimes to improve efficiency with LLMs; without them, LLM performance will not improve. Improving LLM efficiency does not just involve upgrading to newer LLMs or fine-tuning existing models but also includes building the infrastructure and recording inputs, outputs, and performance metrics to study what is working and what is not. You can use frameworks like DSPy that we discussed in section 5.5.2 to capture these items and to identify and handle the cases that do not work or start failing over time as world circumstances change. For example, you might develop an initial LLM that is working well. But those damn kids keep adding new emojis to the iDroids and appleBots [9]. Without additional training, your LLM will not understand these new emojis, but your customers will inevitably start using them, so the system will start performing poorly. You’ll never figure this out if you don’t record the input and output of the LLM in logs or solicit feedback from your users who can provide information about areas where the LLM is failing or succeeding. Capturing this information is essential for improving and refining the process, which LLMs cannot do without human intervention.

</details>

在机器学习领域，数据漂移这一概念备受关注，它指的是现实世界中的数据不断演变，超出了模型训练数据所捕捉的范围。在处理自然语言时，表情符号只是现实世界数据随语言使用演变而随时间变化的一个具体例子。这个表情符号的例子可以扩展到包括新术语或语言中现有词汇新用法所带来的问题。通过审视该领域的现有工作，我们可以识别出其他用于测量和缓解大语言模型数据漂移的技术，例如收集额外的训练数据并对模型进行微调，或修改提示词以包含对先前未见术语的补充定义。

<details>
<summary>英文原文</summary>

In the ML field, considerable attention is given to the concept of data drift, where data in the real world constantly evolves beyond what is captured in a model’s training data. When dealing with natural language, emojis are just one concrete example of how real-world data will change over time as language use evolves. The emoji example can be extended to include the problems created by new terminology or new ways of using existing words in a language. By looking at the existing work in the field, we can identify additional techniques for measuring and mitigating data drift for LLMs, such as collecting additional training data and fine-tuning models or altering prompts to include supplementary definitions for previously unseen terminology.

</details>

### 7.3 语言模型不是世界模型

你经常可以从LLM中引出关于世界的准确信息。因此，很容易认为语言模型了解世界上的事物。事实上，作为本书的读者，你可以在不采取任何具体行动的情况下推理世界和将要发生的事情。现在，我们讨论的不是预测股市这样复杂的事情，而是简单的行动和想法。例如，如果你告诉别人他们的毛衣很丑，会发生什么？

你不需要与环境互动，也不需要找一件丑毛衣来回答这个问题。你不需要说话或与任何人或任何事物互动来回答这个问题。你可以想象毛衣的“世界”和他人可能有的感受，并推断结果。如果我告诉你有人穿着那件毛衣在圣诞派对上（也许是丑毛衣比赛？），你可以更新你对世界的心理模型，并在没有亲身经历的情况下推断结果。LLM无法在说话之前思考。生成文本是LLM最接近“思考”的方式（在此语境下宽松地使用这个词）。你可以在图7.6中看到一个简单的例子，其中LLM过于冗长的推理最终导致它得出一个不错的评论。推理，无论是人类隐式还是显式地进行，都与我们谈论推理对象的行为不同。对于LLM，过程没有分离；需要产生更多输出来对答案进行“更多思考”。因此，LLM无法独立于生成输出而进行思考。

<details>
<summary>英文原文</summary>

You can frequently elicit accurate information about the world from an LLM. As a result, it’s easy to assume that a language model knows things about the world. Indeed, as a reader of this book, you can reason about the world and what will happen without taking any particular action. Now, we are not discussing anything so sophisticated as predicting the stock market, but even simple actions and thoughts. For example, what would happen if you told someone their sweater was ugly? You do not need to interact with the environment or find an ugly sweater to answer this question. You do not need to speak or interact with anyone or anything to answer this question. You can imagine the “world” of sweaters and the feelings someone else may have and infer the results. If I told you someone was wearing the sweater at a Christmas party (an ugly sweater contest, perhaps?), you could update your mental model of the world and infer outcomes without having lived them. An LLM cannot think before it speaks. Generating text is the closest an LLM gets to “thinking” (using the word loosely in this context). You can see a simple example of this in figure 7.6, where an LLM’s overly verbose reasoning ultimately leads it to reach a nice comment. Reasoning, whether done implicitly or explicitly by us humans, is distinct from us speaking about the thing we are reasoning about. For an LLM, there is no separation of processes; producing more output is required to “think more” about the answer. Therefore, LLMs are not capable of thought independent from generating output.

</details>

警告：我们在大语言模型语境中松散地使用“思考”一词。严谨来说，我们指的是大语言模型回答问题所做的计算并非动态变化。无论这10个词元的内容如何，输出它们所需的工作量是相同的。回答一个需要人类更多思考的复杂问题，可能要求大语言模型执行更多的计算，但这通常意味着模型必须生成更长的输出，即使答案本不应更长。每当有人将“思考”一词与大语言模型关联使用时，最好用“计算”替换“思考”。

<details>
<summary>英文原文</summary>

WARNING We loosely use the word “think” in the context of an LLM. To be pedantic, we mean that the calculations an LLM does to answer a question are not dynamic. Outputting 10 tokens takes the same amount of work regardless of the content of those tokens. Answering a complex problem that requires humans to think more will probably require an LLM to perform more compu-tation, but that usually means the LLM must also produce longer output, even if the answer shouldn’t be any longer. Whenever anyone uses the term thinking in conjunction with an LLM, it is better to replace thinking with calculating.

</details>

![图 7.6 LLM能够正确识别某人穿着或做某件不寻常事的上下文和原因，并给出恰当回应。然而，如果不生成一些中间文本，LLM可能无法得出该恰当回应。对于数学问题，这些中间文本可能很有用，但中间文本可能并不总是适合或用户希望看到。](assets/fig-7-6-2f3bc354d6.png)

*图7.6 LLM能够正确识别某人穿着或做某件不寻常事的上下文和原因，并给出恰当回应。然而，如果不生成一些中间文本，LLM可能无法得出该恰当回应。对于数学问题，这些中间文本可能很有用，但中间文本可能并不总是适合或用户希望看到。*

这个例子表明，大语言模型无法在不生成关于规划过程的文本的情况下进行规划。如果大语言模型不产生文本，它就好像不存在一样。有一些构建提示的方法，可以鼓励大语言模型分解其输出以模拟规划。这通常被称为思维链（CoT）提示，即在提示中包含像“让我们逐步思考”这样的陈述。这种逐步的指令通常会提高模型执行任务的能力[10]，但尚不清楚为什么它会提高性能。再次强调，“思考”一词的模糊性可能导致对LLM能做什么和不能做什么产生不合理的期望。

即使使用CoT，大语言模型仍然会犯很多错误，例如遗漏步骤、遗漏计算以及进行逻辑无效的推理[11]。其他因素也可能导致观察到的大语言模型在输出被分解为一系列步骤时的性能提升。考虑：

<details>
<summary>英文原文</summary>

This example demonstrates that an LLM cannot plan without generating text about the planning process. If the LLM is not producing text, it is as if it does not exist. There are methods for constructing prompts that will encourage LLMs to break down their outputs to simulate planning. This is often called chain-of-thought (CoT) prompting, where you include in the prompt a statement like “Let’s think step by step.” This step-by-step instruction often improves the model’s ability to perform tasks [10], but it is unclear why this improves performance. Once again, the ambiguity of what it means to “think” can cause unreasonable expectations of what LLMs can and cannot do.

Even with CoT, LLMs will still make many mistakes, such as missing steps, missing calculations, and performing logically invalid reasoning [11]. Other factors may contribute to the performance gains observed when an LLM produces output broken into a series of steps. Consider:

</details>

第三章中，我们学习了Transformer及其实现中所使用的注意力机制。我们知道，LLM接收的输入和产生的输出越长，Transformer执行的计算就越多。

那么，一步一步思考之所以效果更好，仅仅是因为LLM通过Transformer进行了更多计算吗？如果LLM拥有世界模型，它就可以在不生成输出的情况下完成关于输出的计算。

LLM反映了其训练数据的性质。训练数据中可能有与“一步一步思考”相关的内容，以及其他更冗长且通常正确的内容的教学材料。最终，我们可能手动将LLM的模糊回忆与更相关的训练文档对齐，而不是让LLM执行一个根本不同的功能。

<details>
<summary>英文原文</summary>

Back in chapter 3, we learned about transformers and the attention mechanism used in their implementations. We learned that the longer the input received and outputs produced by an LLM, the more calculations the transformer does.

So does thinking step by step work better just because the LLM, via the transfor-mer, gets to do more computation? If the LLM had a world model, it could do this computation about the output without generating the output.

LLMs reflect the nature of their training data. There may be content in that training data correlated with “think step by step” and other pedagogical materi-als with more verbose and usually correct content. Ultimately, we may manually align the LLM’s fuzzy recall with more relevant training documents rather than get the LLMs to perform a fundamentally different function.

</details>

WARNING “世界模型”的确切定义尚未达成共识，不同的人可能有不同的理解。讨论世界模型时，最好先明确其定义，以便大家达成一致。许多关于LLM的讨论都未能有效沟通，我们将在本书最后两章进一步探讨这个问题。

<details>
<summary>英文原文</summary>

WARNING The precise definition of a “world model” is not yet well agreed upon and can have different connotations for different people. When discussing world models, it is a good idea to discuss the definition first so that folks are on the same page. A lot of LLM discourse talks past each other, something we will discuss further in the last two chapters of this book.

</details>

这些问题具有挑战性，涉及开放性的研究课题。我们的立场是，LLM的显著失败表明，这些更可能是表面解释而非深层次原因。重要的是，一些小众研究致力于为机器学习方法注入世界模型。David Ha和Jürgen Schmidhuber在2018年提供了一个技术性但相当易懂的示例，可在网上获取（https://worldmodels.github.io/），该示例显示了相较于当时现有方法的巨大性能提升。其他人正在致力于为LLM构建世界模型，以及利用LLM作为世界模型[12]。当前的方法不具备人类那样高度的灵活性；这些示例的范围更为有限，仅适用于某一通用类别的问题。

<details>
<summary>英文原文</summary>

These problems are challenging and involve open-ended research questions. Our stance is that the dramatic failures of LLMs highlight that these are more likely explanations than something deeper. Importantly, some niche research focuses on imbuing machine learning methods with world models. A technical but fairly accessi-ble 2018 example of this from David Ha and Jürgen Schmidhuber is available online (https://worldmodels.github.io/) and shows massive performance improvements compared with existing methods back then. Others are working on making world models for LLMs and using LLMs as world models [12]. Current methods do not have the same high degree of flexibility as humans; these examples are more limited in scope and work for one general class of problems.

</details>

### 7.4 计算极限：难题依然难解

有些人担心“失控”的人工智能，即AI算法变得非常先进和强大，能够解决我们从未能解决的问题，并且这样的AI的目标可能与人类福祉不一致。如果这样的AI存在，它可能以我们无法自我改进的方式自我改进，从而产生更强大的AI。许多人放任这种想法蔓延，幻想LLM几乎会拥有神一般的能力，能够超越人类的推理。这里涉及一个伦理问题，我们将在本书最后一章进一步讨论。目前，有一个简单的技术原因让我们不那么担心这个想法，它也有助于我们理解LLM的实际局限性。本质上，有多种方法可以衡量我们所谓的计算复杂度或算法复杂度。通过将LLM的复杂度与其他经过充分研究的算法进行比较，我们可以更具体地了解LLM能做什么和不能做什么。我们还将讨论在适当的情况下，使用LLM进行问题的近似求解如何避免精确求解同一问题所需的某些复杂度。

在计算机科学中，我们花大量时间学习算法复杂度。对大多数学生或从业者而言，这意味着理解输入数据量的变化如何影响一个过程产生结果所需的时间。一种比较理想但现实中很少见的情况是，如果输入翻倍，过程耗时也翻倍。换句话说，对n个项目（在LLM中，一个项目可能是一个token）需要2天的过程，对2×n个项目需要4天。在计算机科学中讨论复杂度时，我们常使用称为大O记号的数学符号来表示不同的复杂度级别。当一个过程的计算时间与输入大小同比例增长时，称为线性复杂度，在大O记号中记为O(n)。如果以数据大小为x轴、计算时间为y轴画图，会得到一条直线，因为数据大小和计算时间以相同速率增长。其他常见的现实世界复杂度包括对数线性（O(n log n)），其中2×n可能接近4.4天；平方（O(n²)），其中2×n可能接近8天；和指数（O(eⁿ)），随着输入大小增加，计算时间增长极快，很可能你的算法完成之前世界就不复存在了。在每种情况下，输入大小对计算时间的图形随着系统变得更复杂而变得更陡峭。换句话说，对于更复杂的算法，处理时间随着处理数据量的增加而增长得更快。

我们进行这一简短的计算机科学之旅，是为了帮助您理解运行LLM的计算复杂度。对于n个项目的输入，LLM的计算复杂度为O(n²)或平方复杂度。如果我们能证明某个算法/任务需要超过O(n²)的工作量，那么我们就基本证明了LLM无法高效解决该问题，因为LLM的核心算法无法精确执行该复杂度的算法。

<details>
<summary>英文原文</summary>

Some people are worried about “runaway” AI, where an AI algorithm becomes so advanced and capable that it can solve problems we never could and that such an AI would not have objectives that align with human welfare. If such an AI existed, it could improve itself in ways we couldn’t improve ourselves, resulting in an even more powerful AI. Many folks have allowed this thought to run rampant, imagining that an LLM will become almost godlike in capability and ability to outreason humans. There is an ethics question here that we will discuss more in the last chapter of the book. For now, there is a simple technical reason why we are not so concerned about this idea, and it also helps us understand the realistic limitations of LLMs. Essentially, there are many ways to measure what we can call computational complexity or algorithmic complexity. By comparing the complexity of LLMs with other well-studied algorithms, we can be more specific about what LLMs can and cannot achieve. We will also discuss how approximate solutions to problems using LLMs can, where appropriate, avoid some of the complexity of precise solutions to the same problems. In computer science, we spend a lot of time learning about algorithmic complexity. For most students or practitioners, this means understanding how a change in the amount of input data changes how long it will take a process to produce results. One of the more ideal cases, which rarely happens in reality, is that if you double the inputs, the process will take twice as long. In other words, a process that could take 2 days for n items (in the case of an LLM, an item might be a token) takes 4 days for 2 × n. When discussing complexity in computer science, we often use mathematical notation, known as Big-O notation, to communicate different levels of complexity. When a process’s computation time grows at the same rate as the size of its input, it is called linear complexity and is denoted in Big-O notation as O(n)). If you draw a graph with data size on the x-axis and computation time on the y-axis, you would get a line because both data and computation time grow at the same rate. Other common real-world complexities include log-linear (O(n log n)), where 2 × n might be closer to 4.4 days; quadratic (O(n2), where 2 × n might be closer to 8 days; and exponential (O(en)), where computation time grows so quickly as the size of the input increases that there is a good chance the world will no longer exist before your algorithm finishes. In each of these cases, the graph of input size versus computation time becomes steeper as systems get more complex. In other words, for more complex algorithms, the processing time will grow faster as the amount of data processed increases.

We’ve taken this short trip into computer science to help you understand the computational complexity of running an LLM. For an input of n items, the LLM has a computational complexity of O(n2) or quadratic complexity. If we can prove that an algorithm/task takes more than O(n2) work, then we have essentially proven that an LLM cannot efficiently solve the problem because an LLM’s core algorithms aren’t able to execute algorithms with that level of complexity, precisely.

</details>

警告：这不是关于形式化方法或算法的研究生课程；我们只是对算法复杂性的研究做一个快速概述。

目的是让读者你获得对这个问题的一种技术直觉，但我们并未完全赋予你详细讨论这个主题所需的所有知识。要了解更多关于算法和复杂性的内容，请参阅Aditya Y. Bhargava的著作《Grokking Algorithms: An Illustrated Guide for Programmers and Other Curious People》[13]。

<details>
<summary>英文原文</summary>

WARNING This isn’t a graduate class on formal methods or algorithms; we are providing a quick overview of the study of algorithmic complexity. The goal is to give you, the reader, a technical intuition for the problem, but we haven’t fully armed you with all the knowledge needed to discuss this subject in detail. To learn more about algorithms and complexity, see Aditya Y. Bhargava’s book Grokking Algorithms: An Illustrated Guide for Programmers and Other Curious People [13].

</details>

如果一个LLM能够解决一个需要，比方说，立方复杂度O(n³)的问题，而它自身的复杂度却更快（更小）为O(n²)，那么这就构成了一个逻辑矛盾。换句话说，LLM解决复杂问题的速度不可能超过其复杂度分析所表明的极限。许多现实世界的任务和算法的复杂度比O(n)更差。我们在表7.1中描述了几个例子，你会注意到我们列出的这些例子都与物流或资源分配相关。例如，包裹递送和航班重新调度都是算法复杂度极其棘手的问题。

<details>
<summary>英文原文</summary>

If it was possible to get an LLM to solve a problem that required, say, cubic complexity of O(n3), but the LLM itself had a faster (smaller) complexity of O(n2), then we would have a logical contradiction. In other words, an LLM can’t solve a complex problem faster than the complexity analysis states. Many real-world tasks and algorithms have worse than O(n) complexities. We describe a few examples in table 7.1, and you’ll notice that the handful we’ve listed relate to logistics or resource allocation. For example, delivering packages and rescheduling flights are problems that have majorly painful algorithmic complexities.

</details>

*表7.1 一些具有不同时间复杂度的关键算法示例*

<details>
<summary>英文原文</summary>

Table 7.1 Some examples of important algorithms with different time complexities

</details>

### 算法复杂度

质因数分解（用于所有加密）O(e^n)（如果你有量子计算机，仍然是O(n^3)）旅行商问题（用于路线/物流配送）O(e^n) 线性规划（用于可分割资源的分配和网络流）O(n^3) 整数规划（用于不可分割资源的分配）O(e^n) 我们关注算法的第二个重要且相关的原因是算法的复杂度类。

复杂度类定义了算法能够解决的问题范围。最著名的复杂度类是P（多项式时间）和NP，后者是指至少需要O(e^n)时间才能完成的问题。这两个非常广泛的类别几乎包含了你可能关心的所有问题。

<details>
<summary>英文原文</summary>

Prime factorization (used for all cryptographic) O(en) (or if you have a quantum computer, still O(n3)) Traveling salesman problem for routing/logistics delivery O(en) Linear programming, used for allocation of divisible resources and network flow O(n3) Integer programming, used for allocating nondivisible resources O(en) A second important and related reason we care about algorithms is the complexity class of an algorithm. A complexity class defines the scope of possible algorithms that an algorithm can solve. The most famous complexity classes are P (for polynomial) and NP, which are problems that take at least O(en) time to finish. These very broad classes contain basically all the problems you might ever care about.

</details>

注意：许多人认为NP代表非多项式，但这是错误的！

它实际上代表非确定性多项式。

<details>
<summary>英文原文</summary>

NOTE Many people think that NP stands for not-polynomial, but this is false! It actually means nondeterministic polynomial.

</details>

有趣且富有启发性的是，William Merrill 和 Ashish Sabharwal 证明[14]，LLM 解决问题的能力与其在中间步骤中生成的 token 数量相关。对于 LLM 而言，生成一个响应属于一个名为 TC0 的复杂性类（我们知道，计算机科学家的命名能力最差）。这个复杂性类限制性极强，意味着 LLM 几乎什么也解决不了。随着中间步骤 n 变长，最终会达到复杂性类 P。这意味着 LLM 永远无法解决 NP 或更难的现实世界问题！我们把这一切汇总在图 7.7 中，该图展示了这些复杂性类层次之间的关系。

这一发现更具破坏性，因为复杂性类描述的是你能解决的问题的种类，而不是解决效率。例如，为了求解一个复杂度为 n^c 的算法，LLM 必须生成大约 n^c 个 token。然而，LLM 处理 n 个 token 需要 O(n^2) 时间，所以最终你会

<details>
<summary>英文原文</summary>

What is interesting and informative is that William Merrill and Ashish Sabharwal [14] proved that an LLM’s ability to solve problems correlates to the number of tokens it generates in intermediate steps. For an LLM, generating a response falls into a complexity class called TC0 (we know, computer scientists are the worst at naming things). This complexity class is very restrictive, meaning an LLM can barely solve anything. As the intermediate steps n become longer, you eventually reach the complexity class of P. This means an LLM can never solve real-world problems that are NP or harder! We tie this all together in figure 7.7, which shows how these layers of complexity classes relate.

This finding is even more damaging because complexity classes describe the kinds of problems you can solve, not how efficiently you can solve them. For example, an LLM must generate on the order of nc tokens to solve an algorithm that involves nc complexity. Yet, an LLM also needs O(n2) time to process n tokens, so you end up

</details>

从工作地点到家找最短路径、设计计算机芯片的电路布局、在文字处理器中查找和替换

<details>
<summary>英文原文</summary>

Finding the shortest path from work to home Designing the layout of circuits for a computer chip Finding and replacing in a word processor

</details>

![图 7.7 ：计算复杂度的维恩图（假设P≠NP，对极客们来说是个小细节）展示了它们之间的相互关系。上方箭头给出了新的复杂度类能够解决的问题类型的示例。下方箭头显示了LLMs在复杂度方面所处的位置。](assets/fig-7-7-2117b94ff2.png)

*图7.7：计算复杂度的维恩图（假设P≠NP，对极客们来说是个小细节）展示了它们之间的相互关系。上方箭头给出了新的复杂度类能够解决的问题类型的示例。下方箭头显示了LLMs在复杂度方面所处的位置。*

这种复杂度估计并未考虑LLM训练数据以及开发提示词所需的时间，这些提示词是为了让LLM能够成功且无误地执行算法。

<details>
<summary>英文原文</summary>

this complexity estimation does not account for LLM training data and the time required to develop prompts to get the LLM to perform the algorithm successfully without errors.

</details>

### 7.4.1 使用模糊算法处理模糊问题

关于算法和复杂性的这番讨论，听起来可能对LLM非常不利。实际上，只有在你要将LLM应用于需要正确输出的问题时，它才是不利的。如果你的系统连最小的错误都无法容忍，那么你就不应该使用机器学习，更不用说LLM了。

与整个机器学习领域一样，LLM最适合处理模糊问题——这类问题中，正确与错误的界定难以描述。在模糊问题中，通常存在错误是可以接受的；其他流程可以纠正这些错误，或者错误的成本可能小到可以忽略不计。这就是为什么文本和自然语言非常适合LLM。像“Suzy在那封邮件中是什么意思？”或“John是否在他的文本中暗示了什么？”这类问题的答案本质上就是模糊的。人类语言充满了不精确、澄清和重复，这些特性与让LLM解决需要一致且精确答案的问题的难度非常吻合。

<details>
<summary>英文原文</summary>

This discussion about algorithms and complexity may sound very damning for LLMs. In truth, it is only damning if you want to apply LLMs to problems that require correct outputs. If even the smallest error is unacceptable in your system, you should not use machine learning, let alone an LLM.

Like machine learning at large, LLMs work best for fuzzy problems, where what makes something correct or incorrect is hard to describe. In fuzzy problems, it is often the case that it is OK if errors exist; other processes can remediate those errors, or the cost of errors is potentially small enough to ignore. That’s why text and natural language are a good fit for LLMs. The answers to problems like “What did Suzy mean in that email?” or “Did John mean to imply that in his text?” are intrinsically fuzzy. Human language is fraught with imprecision, clarification, and repetition that align well with the difficulty of getting LLMs to solve problems that require consistent and precise answers.

</details>

### 7.4.2 近似解足够应对困难问题

先来反驳一下自己：人类同样无法解决NP难问题，如果“解决”指的是“找到没有更好解的最优解”。我们使用近似方法来处理复杂问题，因为我们清楚它们无法完美求解。

<details>
<summary>英文原文</summary>

To argue against ourselves for a moment, we should also point out that humans cannot solve NP-hard problems when we use solve to mean “arrive at the optimal solution for which no better solution exists.” We use approximations to solve complex problems because we know they are too hard to solve perfectly.

</details>

例如，我们在表7.1和图7.7中提到了旅行商问题，这是一个经典且重要的配送路线规划问题。邮递员希望在不重复路线的情况下，以最短的时间和距离完成所有邮件的投递。从计算角度来看，寻找最优路线是NP难问题，因此只能应用于几百个甚至上千个配送目的地。然而，存在更快的二次算法可以近似求解该问题，并且我们可以证明这些算法给出的路径长度不会超过最短路径的两倍。因此，在现实世界中，我们使用这些技术来获得“足够接近即可”的解决方案。同样，LLM也可能提供“足够接近即可”的解决方案，但它们仍然受到无法高效处理精确问题的限制。

如果不了解LLM的训练数据，我们很难评估它通过近似求解解决复杂问题的能力。考虑一下，国际象棋在理论上的难度甚至超过NP难问题。GPT-3.5 能够下出不错的棋局，足以击败真实的人类棋手[15]，但还达不到专用国际象棋程序“统治所有人类”的水平。这是否表明LLM擅长近似解决非常困难的问题？

很可能不是。首先，ChatGPT 的围棋水平在加入国际象棋作为评估指标后大幅提升（https://github.com/openai/evals/pull/45）。有理由怀疑 ChatGPT 的开发者进行了微调，将国际象棋作为一个明确目标。其次，互联网上充满了可供人们学习和探索的棋局（https://old.chesstempo.com/game-database.html），因此 ChatGPT 很可能在其训练数据中收录了完整的棋局。尽管如此，有趣的是 ChatGPT 能够利用训练数据中的棋局下出不错的国际象棋，将之前见过的场景匹配到未来略有不同的情况。在考虑基于LLM的解决方案何处最有效时，我们推荐以下思维框架：将LLM应用于重复性强、变化幅度小的问题，以最大化其效用。文本摘要、语言翻译、文档初稿撰写以及现有文本检查等应用均属于此类。

深度学习的其他领域也带来了类似的经验教训，在这些领域，人们更容易理解模型内部发生了什么，而在LLM领域则不然。例如，围棋游戏是数十年来AI研究中最持久的挑战之一。AI 直到最近才能够在围棋中击败冠军级选手。与LLM类似，围棋AI通过观察大量对局进行训练。然而，如果你构建一个围棋机器人，它执行异常和/或无意义的走法，它会击败“超人”AI，但会输给人类业余选手[16]。这个例子也凸显了在对抗性环境中使用LLM的风险，在这种情况下，人类应对重大新奇情况的能力远优于当前的AI/LLM。

<details>
<summary>英文原文</summary>

For example, in table 7.1 and figure 7.7, we mentioned the traveling salesman problem, a famous and important problem for delivery route planning. The mail courier wants to deliver everyone’s mail in the minimum amount of time and distance traveled without repeating any routes. Computationally, finding the best route is NP-hard, so you can only apply it to a few hundred or maybe a thousand delivery destinations. However, there are much faster quadratic algorithms that approximate the problem, and we can prove they give us a path that is no worse than 2× the travel distance of the minimum distance route. So in the real world, we use these and other techniques to get “close enough is good enough” solutions. So too can LLMs potentially get “close enough is good enough” solutions, but they are still constrained by the fact that they are inefficient for exact problems. Without an understanding of an LLM’s training data, we have difficulty estimating how well it might solve a difficult problem through approximation. Consider that the game of chess is technically harder than NP-hard. GPT-3.5 can play a decent game of chess that can defeat a real human [15], although not at the “dominating all humans” level that dedicated chess programs can achieve. Does this show that LLMs are good at approximately solving very hard problems? Probably not. First, ChatGPT’s chess game dramatically improved after adding chess as an evaluation metric (https://github.com/openai/evals/pull/45). It’s not unreasonable to suspect that the makers of ChatGPT performed fine-tuning that incorporated chess as an explicit goal. Second, the internet is full of games of chess for people to study and explore (https://old.chesstempo.com/game-database.html), so ChatGPT has likely been trained on full games of chess captured in its training data.

Still, it is interesting that ChatGPT can use what is in its training data to play a reasonable game of chess, matching what it has seen before to slightly different situations in the future. When considering where an LLM-based solution will work best, we recommend this mental framework: apply LLMs to repetitive, mildly varying problems to maximize their utility. Applications such as text summarization, language translation, writing first drafts of documents, and checking existing writing all fit into this category.

Similar lessons come from other areas of deep learning, where it is easier to reason about what is happening inside a model than for LLMs. For example, playing the game of Go has been one of the longest-standing challenges in AI research for decades. AI has only recently been able to beat champion-level players in the game. Like LLMs, Go-playing AIs train by observing many example games. Yet, if you built a Go-playing bot that performed unusual and/or nonsensical moves, it would defeat the “superhuman” AI but lose to human amateurs [16]. This example also highlights the risk of using LLMs in adversarial environments, where humans are far better at dealing with significant novelty in a situation than current AI/LLMs.

</details>

LLM相对于人类的最大优势在于它们的可扩展规模。LLM可以低成本全天候运行，并且扩缩容比培训或裁减人力团队容易得多。

人类更擅长处理高度新颖的情况，这在用户可能怀有敌意（例如试图欺诈）时尤为重要。

我们知道LLM擅长解决与训练数据中见过的相似问题，因此适用于重复性工作。提示工程可能是“教会”LLM新知识的最有效起点，除非你愿意投入大量精力和资金进行数据收集与微调。

LLM无法自我改进，且解决需要特定正确答案的算法问题时效率低下。它们最擅长“模糊”问题，即存在一定范围的满意输出且可接受一定误差的问题。

<details>
<summary>英文原文</summary>

Summary The biggest advantage LLMs have over humans is the scale they achieve. LLMs can run at low cost, 24/7, and be resized to meet demand with far less effort than training up or reducing a human workforce.

Humans are better at handling highly novel situations, which is important if the people interacting with the LLM might be adversaries (e.g., trying to commit fraud).

We know LLMs work well for problems similar to what they have seen before in their training data, making them useful for repetitive work. Prompt engineering is likely the most effective starting point to “teach” LLMs something new unless you can dedicate large amounts of effort and money to data collection and fine-tuning.

LLMs cannot self-improve and are inefficient at solving algorithmic problems requiring a specific correct answer. They work best on “fuzzy” problems where there is some range of satisfying outputs and some amount of error is acceptable.

</details>



---

<a id="ch8"></a>

## 第 8 章: 用大语言模型设计解决方案

8 使用大型语言模型设计解决方案

<details>
<summary>英文原文</summary>

8 Designing solutions with large language models

</details>

### 本章涵盖

- 使用检索增强生成减少错误

- LLM 如何监督人类以减轻自动化偏差

- 使用嵌入增强经典机器学习工具

- 企业与用户双赢的LLM呈现方式

<details>
<summary>英文原文</summary>

- Using retrieval augmented generation to reduce errors
- How LLMs can supervise humans to mitigate automation bias
- Enabling classic machine learning tools with embeddings
- Ways to present LLMs that are mutually beneficial to companies and users

</details>

到现在为止，你应该已经对LLM及其能力有了深刻理解。它们生成的文本与人类文本非常相似，因为它们是经过数亿份人类文本文档训练而成的。它们产出的内容虽然很有价值，但也容易出现错误。而且，如你所知，你可以通过结合领域知识或使用计算机源代码解析器等工具来减轻这些错误。

现在你已经准备好使用LLM来设计解决方案了。如何将我们目前讨论的所有内容转化为一个有效的实施计划？本章将带你了解制定该计划的过程、权衡因素和注意事项。为此，我们将使用一个大家都能感同身受的实例：需要帮助时联系技术支持。

<details>
<summary>英文原文</summary>

By now you should have a strong understanding of LLMs and their capabilities. They produce text that is very similar to human text because they are trained on hundreds of millions of human text documents. The content they produce is valuable but also subject to errors. And, as you know, you can mitigate these errors by incorporating domain knowledge or tools like parsers for computer source code. Now you are ready to design a solution using an LLM. How do you consider everything we have discussed thus far and convert it into an effective implementation plan? This chapter will walk you through the process, trade-offs, and considerations in designing that plan. To do so, we will use a running example that we can all relate to: contacting tech support when help is needed.

</details>

首先，我们考虑一条显而易见的路径：构建一个聊天机器人。聊天机器人是许多人接触大语言模型（LLM）的桥梁，因为它们通常能出色地交互式生成输出。我们将评估在客户服务场景中部署基于LLM的聊天机器人的风险。通过这一讨论，你会看到使用LLM可能会比其他方案带来更高的风险。然而，如果风险足够小，简单的聊天机器人可能是一个可行的选择。

接下来，我们将探讨如何通过改善客户与LLM交互的应用程序设计来管理风险。我们将讨论由于一种称为“自动化偏见”的现象，由人工逐一检查LLM的每个输出会带来很多问题。我们还将讨论如何通过让LLM监督人工来反直觉地避免自动化偏见。我们将探讨如何将LLM的嵌入向量（文本的语义数字表示）与经典机器学习算法结合，以应对这一风险，并处理LLM无法独立完成的任务。

最后，我们将研究技术是如何呈现给用户，并在建立信任和传达其内部工作原理方面发挥关键作用。我们将讨论“可解释AI”领域，即机器学习算法输出描述或解释其如何得出特定结果的内容。可解释AI通常是处理人们需要理解LLM工作原理情况的方法，但研究表明，虽然可解释性可能通过用人类术语描述这些模型的行为来揭示LLM的内部工作机制，但它本身并不能真正起到帮助作用。相反，我们将描述关注透明度、使激励与客户一致、创建反馈循环的好处，从而设计出更符合使用它们的公司和与之交互的客户双方需求的解决方案，提供准确的输出并提高业务流程效率。

<details>
<summary>英文原文</summary>

First, we will consider the obvious path: building a chatbot. Chatbots are the vehicle that introduced many people to LLMs because generally, they can do an excellent job of generating output interactively. We’ll evaluate the risks of deploying an LLM-powered chatbot in a customer service scenario. Through this discussion, you’ll see that using an LLM can increase risk compared to other options. However, a simple chatbot may be a valid option if the risks are sufficiently minimal. Next, we will explore ways to manage the risks by using application designs that improve how customers interact with the LLM. We’ll discuss how having a person check each output produced by an LLM is fraught with problems due to a phenome-non known as automation bias. We’ll discuss how automation bias can be somewhat counterintuitively avoided by having the LLM supervise the person instead. We’ll explore how an LLM’s embeddings, the semantic representation of text encoded as numbers, can be combined with classical machine learning algorithms to address this risk and handle tasks that an LLM can’t perform independently. Finally, we’ll investigate how technology is presented to users and plays a vital role in establishing trust and conveying an understanding of its inner workings. We’ll discuss the area of “explainable AI,” where a machine learning algorithm produces output that describes or explains how it arrived at a specific output. Explainable AI is often the approach adopted to handle situations where people need to understand how an LLM works, but studies show that although explainability may shed some light on the inner workings of LLMs by describing the behavior of these models in human terms, it does not tend to help for its own sake. Instead, we’ll describe the benefits of focusing on transparency, aligning incentives with customers, and creating feedback cycles to design solutions that better meet the needs of both the companies that employ them and the customers that interact with them by providing accurate output and creating efficiencies in business processes.

</details>

### 8.1 只做个聊天机器人？

毫不意外，许多人正基于Transformer架构（ChatGPT的底层技术）构建聊天机器人。这显然是一个看似合理的第一步。ChatGPT与人类交互、适应对话、检索和呈现信息的出色能力，充分展示了LLM技术对客户交互应用的支持有多么强大。随着LLM的出现和普及，尝试用其他方法（例如使用基于决策树预设回复的专家系统）来实现客服代理，很可能是目光短浅的。当遇到技术问题的不开心客户，无需搜索在线FAQ文档、向工单系统的黑洞发送邮件、或拨打自动语音应答的电话，他们可以直接与AI工具互动，逐步解决问题。这听起来纸上谈兵很美妙，如果画个像图8.1那样的小图，确实看起来像是简化了生活。

<details>
<summary>英文原文</summary>

Unsurprisingly, many people are building chatbots using LLMs based on transformer architectures, the same technology that underpins ChatGPT. It’s an obvious and seemingly reasonable first step. ChatGPT’s fantastic ability to interact with people, adapt to conversations, and retrieve and present information demonstrates how well LLM technology supports customer interaction applications. With the advent and availability of LLMs, it would likely be short-sighted to attempt to implement a customer service agent using any other approach, such as using an expert system trained to use a decision tree of canned responses. When an unhappy customer has some technical problem, instead of searching an online Frequently Asked Questions (FAQ) document, sending an email into the black hole of a trouble ticket system, or calling a phone number with an automated interactive voice response system, they can start directly interacting with an AI-powered tool and make progress on getting their problems solved. This sounds wonderful on paper, and if you draw a little diagram like figure 8.1, it sure looks like we are simplifying life.

</details>

![图 8.1 从流程图中看，用基于LLM的聊天机器人替换FAQ、邮件工单和客服电话似乎能简化流程、提升效率。然而，这种观点其实很荒谬，因为它忽略了流程的不完整性。确保LLM准确运行所需的潜在错误和纠错过程被隐藏了，反而引入了更多复杂性。](assets/fig-8-1-2b55f1ea9f.png)

*图8.1 从流程图中看，用基于LLM的聊天机器人替换FAQ、邮件工单和客服电话似乎能简化流程、提升效率。然而，这种观点其实很荒谬，因为它忽略了流程的不完整性。确保LLM准确运行所需的潜在错误和纠错过程被隐藏了，反而引入了更多复杂性。*

聊天机器人在某些场景下确实是好主意。但令人意外的是，基于LLM的在线支持聊天机器人很可能并非大多数公司客户支持工具的首选，因为要构建一个在多数情况下准确可靠、面对意外输入也不会产生异常输出的系统，需要投入大量精力。归根结底，是否使用LLM来实现客户支持聊天机器人，取决于我们一直在讨论的LLM在生成客户回复时可能犯的错误。我们知道LLM并非零错误，虽然机器学习有时很实用，但在考虑部署这项技术时，潜在错误的代价是主要的决策标准。从根本上说，使用LLM可能会增加这些错误的成本。底线是，在目前的形式下，LLM可能提供错误答案，而部署和维护它们的公司或个人需要为此承担责任。

高管或产品经理可能会根据几个经典的业务关键绩效指标来考虑错误成本。例如，如果让聊天机器人负责支持，客户保留率可能会下降。不过，这种保留率可能仍然高于将客户关系职能外包给其他国家呼叫中心的情况。实际上，在完全用LLM取代客户支持功能之前，应该先进行试点部署，了解客户的想法，这些考虑都很重要。

<details>
<summary>英文原文</summary>

There are certainly cases where a chatbot is a good idea. But surprisingly, an online LLM-based chatbot that handles support probably is not at the top of the list of customer support tools for most companies because of the effort required to build a system that will be accurate and reliable in many cases and not create unexpected output when confronted with unexpected input. Ultimately, the decision to use an LLM to implement a customer support chatbot comes down to our ongoing discussion of the errors an LLM might make when generating customer responses. We know that LLMs are not error-free, and while machine learning is sometimes practical, the expense of those potential errors is the primary decision criterion when considering deploying this technology. Fundamentally, using an LLM potentially increases the cost of those errors. The bottom line is that in their current form, LLMs can provide incorrect answers, and the liability for these falls on the shoulders of the companies or individuals who deploy and maintain them.

Executives or product managers might consider the cost of errors in the context of a few classic business key performance indicators. For example, customer retention rates might decrease if they entrust support to chatbots. Perhaps the retention rate would be higher than if the customer relations functions were outsourced to a call center in another country. Indeed, these considerations are important to evaluate, and you should probably do a trial deployment to see what customers think before replacing your customer support function with an LLM wholesale.

</details>

**注意：** 我们几乎总是建议对任何机器学习系统进行试验性部署。

投资界有句格言“过往业绩不代表未来收益”，这对任何AI都适用。实现这一目标的一种方法是“幽灵部署”，即将新AI系统与现有流程并行运行数周或数月。在现有业务流程仍在运行期间，你可以选择忽略新系统的输出结果。这让你有时间观察现有流程与新流程之间的差异，发现并解决问题，并判断机器学习系统的性能是否随时间下降。

<details>
<summary>英文原文</summary>

NOTE We almost always recommend trial deployments of any machine learning system. The investing adage “Past performance is not a guarantee of future returns” is true of any AI. One way to do this is through phantom deployments, where you run your new AI system alongside the existing process for some weeks or months. You may choose to ignore its outcomes while the existing business processes are in place. This gives you time to observe the discrepancies between your current and new processes, identify and address problems, and determine whether the performance of the machine learning system degrades over time.

</details>

最致命的是，你的大语言模型可能会给出对用户造成伤害的建议。由于大语言模型并非可为其行为承担法律责任的人，因此你和你所在的公司将承担责任。这种情况已发生在一家航空公司身上，该公司部署的聊天机器人给出了错误的政策声明。法院裁定该公司必须遵守其聊天机器人错误生成并分享的政策[1]。

我们建议在部署大语言模型时始终保持对抗性思维。思考“一个动机明确的不良行为者如果知道这是如何运作的，会做什么？”将有助于你识别和减轻重大风险，这通常是判断你计划的大语言模型应用是好是坏的最佳方法。例如，一家汽车公司将其网站集成大语言模型，用于协助销售汽车和回答问题。得知此事后，用户在不到一天内就成功让网站以1美元的价格向他们出售汽车[2]。

如果错误的潜在成本或风险较低，你可以放心地部署大语言模型聊天机器人。但为了本章的论述，让我们假设我们设想的这个技术支持代理非常重要，且其错误可能给公司造成巨大损失。现在问题变成了：我们如何设计一个解决方案，既能提高生产力和效率，又能限制用户直接访问大语言模型？如果你刚接触人工智能/机器学习，聊天机器人是你对该领域的主要了解，这可能听起来像是一个矛盾，但有一些简单、可重复的设计模式可以应用来实现这一点。

<details>
<summary>英文原文</summary>

Most critically, your LLM can give advice that causes harm to your users. Since an LLM is not a person who can be held legally liable for their actions, you and your company will be held liable instead. This has already happened with an airline that deployed a chatbot that gave errant policy statements. A court decided that the company had to abide by the policy incorrectly generated and shared by their chatbot [1]. We recommend always considering an adversarial mindset when deploying an LLM. Asking “What could a motivated bad actor do if they knew how this worked?” will help you identify and mitigate significant risks and is often the best way to determine whether your intended LLM application is a good or bad idea. For example, a car company integrated an LLM into their website to help sell cars and answer questions. After realizing this, it took less than a day for users to convince the website to sell them a car for just $1 [2].

If the potential cost or risk of errors is low, you can feel comfortable deploying an LLM chatbot if you so choose. But for the sake of this chapter, let us assume that this technical support agent we are hypothesizing is very important, and the mistakes it makes could cost the company a lot of money. The question now becomes: How do we design a solution that gives us benefits in productivity and efficiency yet limits users’ direct access to an LLM? If you are new to AI/ML and a chatbot is your primary exposure to the field, this might sound like a contradiction, but there are some easy, repeatable design patterns you can apply to do this.

</details>

### 8.2 自动化偏差

应对使用LLM进行直接客户交互风险的一种常见方法是让LLM与支持人员或技术人员互动，而不是直接与客户交互。这种方法通常被称为“人机回环”，因为有一个人员在审查LLM与客户之间的反馈回路，对自动化系统的输出进行关键评估，并在检测到错误时进行干预和调整输出。技术人员仍将被雇佣，但我们会通过让LLM为用户的每个问题生成初始响应，并由技术人员对这些响应进行审核以确保其准确性和相关性，从而提高他们的效率。如果LLM生成了潜在的高成本或不正确的响应，我们可信赖的技术人员将进行干预，并回复更合适的内容。在这种背景下，最终由技术人员选择正确的权威响应。

还记得我们在第5章中关于检索增强生成（RAG）讨论的聪明读者，甚至可能想到改进这一想法的方法。你会说：“啊，我们可以把所有培训手册和文档放入数据库，然后使用RAG，这样LLM就可以检索与用户问题最相关的信息。”这种方法在图8.2中进行了概述，图中展示了一个过程：首先将用户的问题发送给LLM，以便使用已知答案集合来聚焦输出生成。

<details>
<summary>英文原文</summary>

A common approach to addressing the risk of using LLMs for direct customer interactions is to have the LLM interact with support staff or technicians instead. This is often referred to as “human in the loop” because there’s a person who is reviewing the feedback loop between the LLM and the customer, providing a critical assessment of the automated system’s output, and intervening and adjusting the output when they detect an error. The technician will still be employed, but we will increase their efficiency by having the LLM generate an initial response to each question from a user and a technician curating those responses to ensure that they are accurate and relevant. If the LLM generates a potentially costly or incorrect response, our trusty technicians will intervene and reply with something more appropriate. In this context, it is ultimately up to the technician to choose the proper authoritative response.

The clever reader who remembers our discussion about retrieval augmented generation (RAG) from chapter 5 might even identify ways to improve upon this idea. You’ll say, “Ah, we can put all our training manuals and documentation inside a database, and then we can use RAG so that the LLM can retrieve the most relevant information to a user’s question.”. This approach is outlined in figure 8.2, which shows a process where a user’s questions are first sent to the LLM to focus output generation using a collection of known answers.

</details>

![图 8.2 实现“人在回路中”系统的一种朴素方法，该系统使用LLM与相关信息数据库配对，生成输出，最终由人工工作人员审查并可能纠正。](assets/fig-8-2-dfa98dbd89.png)

*图8.2 实现“人在回路中”系统的一种朴素方法，该系统使用LLM与相关信息数据库配对，生成输出，最终由人工工作人员审查并可能纠正。*

RAG方法可能会缓解大量风险，但也有可能陷入自动化偏差的陷阱。自动化偏差指的是，人们通常倾向于选择系统提供的自动化或默认选项，因为这比运用批判性思维来判断哪个选项最适合当前情况更容易。如果系统运行良好，不需要你经常干预，那么保持高度警惕并发现偶尔的错误就变得极具挑战性。矛盾之处在于，如果系统建议的准确率低到足以让你保持警惕，那么与完全不使用自动化直接回答问题相比，系统很可能反而拖慢你的速度。

这正是试运行或模拟部署变得极为重要的地方。如果你的系统准确度如此之高，以至于自动化偏差才是真正的风险来源，那么你有两个选项，且无需偏离“人在回路中”的设计：

<details>
<summary>英文原文</summary>

The RAG approach will likely mitigate a lot of risk, but it also has the potential to hit the pitfall of automation bias. Automation bias refers to the fact that people, in general, tend to pick automated or default choices presented by a system because it is easier than applying critical thinking to determine which choice is most appropriate to the situation at hand. If a system works well and does not need you to intervene often, it becomes incredibly challenging to remain hypervigilant and detect the occasional error. The paradox is that if the system is so inaccurate in its suggestions that you can maintain your vigilance, the chances are good that the system is slowing you down when compared to directly answering questions using no automation. This is where trial or phantom deployments become incredibly important. If your system is so accurate that automation bias is the real source of risk, you have two options that do not require deviating from the “human in the loop” design:

</details>

在流水线中增加一条“转人工”路径，通过流程变更从外部降低错误风险。第一点相当直接。最终，总会出现大语言模型无法回答的新情况。在这种情况下，最好为客户提供一种方式，让他们能够从与计算机的无限循环中“逃脱”，从而获得更高级别的支持。这可以是消息交换次数或聊天时长限制的最大对话长度，也可以是在多次沟通尝试失败后出现的联系人工代表的选项，或者其他可能的设计方案。

<details>
<summary>英文原文</summary>

Add an “escape to a human” path to the pipeline Mitigate the risk of errors externally via process changes The first point is pretty straightforward. Eventually, a novel situation will occur that the LLM cannot answer. In this case, it would be best to provide a way for a customer to “escape” from an infinite loop with a computer to get to a higher tier of support. This could be a maximum conversation length measured in the number of messages exchanged or the amount of time spent chatting, an option to contact a human representative that appears based on multiple failed attempts to communicate, or other possible designs.

</details>

注意 假设你打算按照第5章讨论的方式，创建一个RLHF或SFT数据集，以针对你的场景微调LLM。

在这种情况下，你甚至可以添加训练样本，让LLM的预期响应为：“抱歉，这个情况听起来比我所能协助的要复杂；让我找个人来帮忙。”

<details>
<summary>英文原文</summary>

NOTE Suppose you are going to do the work to create an RLHF or SFT dataset to fine-tune your LLM to your situation as we discussed in chapter 5. In that case, you can even add training examples where the LLM’s expected response is “I’m sorry, this situation sounds more complex than what I can assist with; allow me to get a human to help.”

</details>

### 8.2.1 变更流程

第二个建议，即改变流程，其实并没有听起来那么难。如果你的某位上司拥有MBA学位，他们（据说）受过这方面的训练。（本书作者之一也拥有MBA，所以我们这样说没问题。）例如，与聊天机器人的交互可以包含一条警告，即任何结果都需要“人类最终批准”。在这种情况下，让一个人检查整个对话，相比于要求某人在整个连续对话中保持持续警惕，自动化偏见的风险要小得多。最终，对抗性用户知道会有人类进行核查，因此他们就不太有动力去尝试欺骗系统。

根据上下文，防止LLM被恶意使用可以通过要求用户提供抵押品来确保其诚信行为。例如，你可以采取相当于冻结用户信用卡的措施，作为防止恶意互动的保险。当交易成功完成时，这笔冻结将被解除。你还可以限制流程的自动化程度、要求身份验证，或者随机分配用户是接入人工客服还是AI客服，从而使可能出现可被利用情况的时间变得不可预测。

所有这些措施都取决于你的具体应用、风险、对这些风险的承受能力以及用户的性质。一些客户可能会因为信用卡冻结而感到不满。或者，你可以将其设计为一种可选方式：如果AI系统成功帮助用户解决了问题，用户将在账单上获得2美元的折扣——前提是这笔费用低于旧系统每次通话的成本。无论哪种方式，都要视具体情况而定，并取决于你管理风险的创意。

<details>
<summary>英文原文</summary>

The second suggestion, changing the process, is not as difficult as it may sound. If one of your bosses has an MBA, they are (allegedly) trained to think in these terms. (One of the authors has an MBA, so it is OK for us to say that.) For example, interactions with the chatbot could include a caveat about any outcome requiring “a human’s final approval.” In this case, having the entire conversation reviewed by a person is far less of an automation bias risk than requiring someone to maintain constant vigilance throughout a continuous conversation. Ultimately, adversarial users know a human is going to check and so are demotivated from trying to game the system. Depending on the context, preventing adversarial use of an LLM can be achieved by requiring the user to provide collateral to ensure they act in good faith. For example, you could take actions equivalent to putting a hold on the user’s credit card as a kind of insurance against bad-faith interactions. Such a hold would be released when the transaction is completed successfully. You could also limit how much of the process is automated, require authentication, or randomize how often people are routed to a human versus an AI so that it becomes unpredictable when a situation that could be exploited will arise.

All of these actions will depend on your specific application, the risks, the tolerance of those risks, and the nature of your users. Some customers might be turned off by a credit hold and be upset. Or maybe you frame it as an optional method in which the user gets $2 off their bill if an AI system successfully helped them with their problem, presuming that it is less than what the old system would have cost per call. Either way, it is case by case and will depend on your creativity to manage the risk.

</details>

### 8.2.2 当自主LLM风险过高时

至此，你已完成试部署，评估了风险以及用户的对抗性倾向，并得出结论：让LLM提供初始答案风险过高。那么，LLM还能在某种程度上提升效率吗？

一种非直观的方法是让LLM检查人，而不是让人检查LLM。这听起来可能有些奇怪。如果我们不能信任LLM独立行动，为何还要让它担任监督角色？为了深入探讨这一点，设想一个由LLM系统担任监督角色的场景，它负责检查每一条回复，如图8.3所示。

如果LLM和人都认为回答正确，那么将采取行动，消息会传递给客户。这就像用户直接与技术人员聊天一样。但如果技术人员与LLM对答案有分歧，我们可以提示技术人员在发送给用户之前再次核实自己的回复。

<details>
<summary>英文原文</summary>

So now you have done a trial deployment, evaluated the risks and your users’ adver-sarial proclivities, and concluded that it is too risky for LLMs to provide the initial answers. How could an LLM still provide some level of efficiency? An unintuitive approach is to have the LLM check the person rather than the person check the LLM. This may sound strange. Why would we let the LLM supervise if we cannot trust it to act alone? To consider this further, imagine you have an LLM system in this supervisory role, checking each response, as shown in figure 8.3. If the LLM and the person are correct, action will be taken, and the message will be relayed to the customer. It will be as if the user is chatting with the technician. But if the technician and the LLM disagree on the answer, we can prompt the technician to double-check their response before sending it to the user.

</details>

![图 8.3 注意该图中的箭头方向与图8.2相比发生了变化。所有内容先交给人类，我们利用LLM在错误发生前将其捕捉。](assets/fig-8-3-eacc87577f.png)

*图8.3 注意该图中的箭头方向与图8.2相比发生了变化。所有内容先交给人类，我们利用LLM在错误发生前将其捕捉。*

这种复核可以简单到告诉技术人员：“嘿，这个可能对解决方案来说不太正常，请确认后再发送。”你也可以尝试让LLM自己生成备选方案。或者，你可以让LLM不直接参与，而是用它来通知更有经验的技术人员加入并协助。无论采用何种结构，其目的都是要提示可能存在导致客户体验不佳的风险，例如提供错误答案。虽然这种风险以前就存在，但现在我们有机会加以缓解。

此外，由于我们考虑的是人为导致的客户支持错误，因此总体上我们并未承担任何新风险，因为支持代表单独行动时同样容易出错。所以，如果LLM和人类同时出错，那这个流程错误本来也注定会发生。这就是生活。从技术上讲，我们可以认为技术人员可能会因LLM对其交互的评估而过度质疑自己的回复，从而降低效率。此外，过于敏感的LLM可能会频繁要求技术人员复核其工作，导致警报疲劳，进而使技术人员完全忽略LLM的建议。如果你的用例容易出现此类问题，那么这一事实将在试运行部署中暴露出来，试运行会提供上下文特定的反馈，说明应如何调试LLM以解决该问题。适用于所有机器学习的通用告诫在此尤为重要：始终测试，不要臆断。

<details>
<summary>英文原文</summary>

This double-check could be as simple as telling the technician, “Hey, this looks like it may be abnormal for a solution; please confirm before sending.” You could try having the LLM produce its own suggested alternative. Or you could keep the LLM out of the process and use it to notify a more experienced technician to join the process and assist. Regardless of how this is structured, the purpose is to signal that there may be a risk of a negative customer interaction, such as an incorrect answer. While this risk existed previously, we now have a chance to mitigate it. Additionally, because we are considering human-initiated customer support errors, we are generally not taking on any new risk because a support representative acting alone could just as easily make a mistake. So if the LLM and human are both wrong simultaneously, you were already doomed to make that process error anyway. Such is life. Technically, we could argue that technicians could question their responses too much based on an LLM’s assessment of their interactions, thus reducing efficiency. Additionally, an overly sensitive LLM may ask technicians to double-check their work too often, which would cause alert fatigue that could lead to technicians ignoring the LLM suggestions entirely. If your use case is prone to these sorts of problems, that fact will be uncovered during trial deployments that provide context-specific feedback on how an LLM should be tuned to address this problem. The general caveat that applies to all machine learning is especially important here: always test; do not assume.

</details>

使用LLM复核人工表现可以在整个过程中减少错误。这种方法似乎不会加快任何速度，因为人工仍然在生成初始回复。然而，这种方法仍然创造了提高效率的机会：

<details>
<summary>英文原文</summary>

Employing an LLM to double-check human performance can reduce errors in the process as a whole. It may not seem like this approach makes anything faster because humans are still generating the initial response. However, this approach still creates opportunities for increased efficiency:

</details>

它可以通过帮助发现错误并更快地找到解决方案来缩短对话长度。

它可以识别需要更多培训或信息才能回答客户问题的员工，或者识别特定错误情况的发生。它可能有助于避免将问题升级到成本更高的支持层级或管理人员，从而减少麻烦客户的频率和成本。

<details>
<summary>英文原文</summary>

It can reduce the conversation length by helping to catch errors and reach a solution faster.

It can identify staff who need more training or information to answer customer questions or recognize when specific error situations occur. It may help avoid escalation to more costly levels of support or managers, reducing the frequency and cost of troublesome customers.

</details>

### 8.3 使用LLM以外的工具降低风险

我们之前讨论的所有内容都涉及一种“以毒攻毒”的方法，即尽管使用LLM存在风险，但我们已经考虑了多种利用LLM来减轻这些风险的方法。虽然我们改变了使用LLM的方式，但LLM仍然是主要组件。或者，我们可以考虑使用LLM之外的工具来应对设计挑战。生成式AI范围内的其他方法，如文语转换和语音转文本，可用于构建更易访问或更便捷的用户体验。例如，患有关节炎或视力低下的用户可能更倾向于打电话，而不是在聊天机器人输入框中打字回复。

如果我们思考客服问题以及LLM何时能良好运作，就会发现更广泛工具类型的要素也已然具备。LLM在问题重复出现、可以用公式化解决方案和回复的场景中表现最佳。LLM在识别语言模糊性中的广泛模式方面非常灵活。如果LLM能正确理解用户的问题，并且存在已知的解决方案，它就有可能引导用户逐步完成该解决方案。这听起来很像无监督聊天机器人，但关键区别在于，在LLM扮演辅助角色的场景中，最终输出是由客服技术人员生成的，如图8.3所示。

本节还将讨论如何利用经典机器学习技术（如分类）来解决现有问题。我们可以通过利用LLM内部的知识，生成用户文本的嵌入表示，从而启用机器学习技术。

<details>
<summary>英文原文</summary>

Everything we have discussed has involved a “fight fire with fire” approach in which, although there are risks to using LLMs, we have considered different ways to use LLMs to mitigate those risks. While we’ve changed how we use the LLM, the LLM is still the primary component. Alternatively, we can consider using tools other than LLMs to address our design challenges. Other approaches in the scope of generative AI, such as text-to-speech and speech-to-text, can be used to build more accessible or simply convenient user experiences. For example, users with arthritis or low vision may greatly prefer a phone call over typing responses into a chatbot prompt window. If we think about our customer service problem and when LLMs work well, we will discover that the ingredients for a broader class of tools are also available. LLMs work best when there is repetition in scenarios where problems reoccur and formulaic solutions and responses can be given. LLMs are very flexible in recognizing broad patterns in the fuzzy nature of language. If the LLM can correctly interpret a user’s problem, and there is a known solution, it can potentially walk a user through that solution. This might sound much like an unsupervised chatbot, but the critical distinction is that in the cases where the LLM takes a subordinate role in the solution, the output was ultimately generated by customer support technicians, as described in figure 8.3.

This section will also discuss how we can use classic machine learning techniques, such as classification, to tackle existing problems. We can do this by using the knowl-edge within LLMs to enable machine learning techniques by producing embeddings of the user’s text.

</details>

### 8.3.1 将LLM嵌入与其他工具结合

在第3章中，我们描述了LLM如何将词元转换为嵌入向量，这些向量是编码每个词元语义表示的一系列数字。这些向量嵌入在LLM的Transformer架构之外的其他方面也很有用。虽然向量嵌入对于LLM的运行至关重要，但它们本身也是一个极其有用的工具。

<details>
<summary>英文原文</summary>

In chapter 3, we described how an LLM transforms tokens into embeddings, which are vectors that encode a semantic representation of the meaning of each token as a series of numbers. These vector embeddings are useful in other ways outside the context of LLM’s transformer architecture. While vector embeddings are essential for making the LLM operate, they are themselves an extraordinarily useful tool.

</details>

LLM 产生的向量具有语义性质，这一点很重要，因为数以百计的实用机器学习算法都在操作向量表示。LLM 本质上是一种将复杂的人类语言文本转换为机器学习领域其他部分兼容形式的强大方式。

将 LLM 的向量输出与其他算法结合使用是一种极为有效的策略，从业者称之为“创建嵌入”。这一说法源于 LLM 将一个表示（人类文本）嵌入到另一个表示（数学向量）中的思想。

由于这些数字编码了原始文本的信息，你可以像对待数字一样绘制它们，并看到相似的文本在图上落在相似的位置，如图 8.4 所示。

<details>
<summary>英文原文</summary>

The semantic nature of the vectors produced by LLMs is important because hundreds of other practical machine learning algorithms operate on vector repre-sentations. LLMs are essentially a very powerful way of converting complex human language text into a form compatible with the rest of the machine learning field.

Utilizing the vector outputs of LLMs with other algorithms has been such an extraor-dinarily useful strategy that practitioners will describe it as “creating embeddings.” The description comes from the idea that the LLM is taking one representation (human text) and embedding it into another representation (a mathematical vector).

Because these numbers encode information about the original text, you can plot them like numbers and see that similar texts end up in similar locations on the plot, as shown in figure 8.4.

</details>

![图 8.4 LLM作为其功能的内在部分，会生成称为嵌入的数字向量。这些嵌入的效用取决于一个事实：当输入相似文本时，这些数字的变化很小。这里的两个示例测试将具有相似的嵌入，因此它们的图看起来相似，即使它们不共享任何相同的单词。这是早期机器学习技术中就存在](assets/fig-8-4-df4429d3d1.png)

*图8.4 LLM作为其功能的内在部分，会生成称为嵌入的数字向量。这些嵌入的效用取决于一个事实：当输入相似文本时，这些数字的变化很小。这里的两个示例测试将具有相似的嵌入，因此它们的图看起来相似，即使它们不共享任何相同的单词。这是早期机器学习技术中就存在的强大特性。*

我们快速了解一下四种机器学习算法——在获得嵌入后即可使用。我们认为每类算法对于LLM的大多数实际应用场景都特别有用；同时，我们也会提到一些相对可靠且易于使用的流行算法。关键要点是：如果你打破只有LLM才能解决问题的思维定势，你将拥有更广泛的工具集。这个列表是你探索其中一些工具的起点地图：

<details>
<summary>英文原文</summary>

Let’s look at a quick description of four types of machine learning algorithms you can use once you have embeddings. We consider each type of machine learning to be particularly useful for most real-world use with LLMs; we will also note some popular algorithms you can find that are relatively reliable and easy to use. The critical takeaway is that if you break out of the mindset that only an LLM can solve a problem, a more extensive set of tools becomes available to you. This list is your starting map for some of those tools:

</details>

- 聚类算法——根据文本间的相似性进行分组，使组内文本与大量可用文本具有显著差异（例如，用于市场细分分析）。常用算法包括K-means和HDBSCAN。

- 异常检测——找出与几乎所有其他可用文本都不相似的文本（即，发现逆向客户或新问题）。常用算法包括孤立森林和局部异常因子（LoF）。

<details>
<summary>英文原文</summary>

Clustering algorithms—Grouping texts by similarity to each other that are distinct from the larger amount of text available (e.g., used for market segment analysis). Popular algorithms include k-means and HDBSCAN.

Outlier detection—Finding texts that are dissimilar from essentially all other texts available (i.e., finding contrarian customers or novel problems). Popular algorithms include Isolation Forests and Local Outlier Factor (LoF).

</details>

- 信息可视化——将数据绘制成二维图形以支持视觉检查/探索，特别是与交互式工具结合使用时（即数据探索）。常用算法包括UMAP和PCA。

- 分类与回归——如果你用已知结果（如净推荐值评分）标记旧文本，可以使用分类（即从A、B、C中选择一个）或回归（即预测一个连续数值，如3.14或42）来预测新文本的分数（即数据分类与价值预测）。使用嵌入向量作为逻辑回归和线性回归等简单算法的输入，分别适用于分类和回归任务。

<details>
<summary>英文原文</summary>

Information visualization—Creating a 2D plot of your data to allow visual inspec-tion/exploration, especially when combined with interactive tools (i.e., data exploration). Popular algorithms include UMAP and PCA. Classification and regression—If you label your old texts with known outcomes (e.g., net promoter score rating), you can use classification (i.e., pick one of A, B, or C) or regression (i.e., predict a continuous number like 3.14 or 42) to predict what the score would be on a new text (i.e., data categorization and value prediction). Using embeddings as input for simple algorithms like logistic regression and linear regression works well for classification or regression, respectively.

</details>

注意：嵌入并非随着大型语言模型而新发明的事物。早在2013年，一种名为Word2Vec的算法（能够嵌入单个词语）就将嵌入推广为表示文本含义的首选策略。

尽管如此，大型语言模型生成的嵌入往往比旧算法更具实用性。然而，大型语言模型的计算需求远高于Word2Vec等旧算法。因此，你可能希望为此任务使用一个更旧或更快的算法。生成式AI方法在图像、视频和语音中的存在意味着，除了文本之外，你还可以将嵌入用于图像、视频和语音等领域。

<details>
<summary>英文原文</summary>

NOTE Embeddings are not something new that was invented as a part of LLMs. An algorithm known as Word2Vec, which could embed single words, popularized embeddings as a go-to strategy for representing the meaning in text back in 2013. Despite this, LLMs tend to produce embeddings with greater utility than other older algorithms. However, an LLM is far more computationally demanding than older algorithms like Word2Vec. For this reason, you may want to use an older or faster algorithm for this task. The existence of generative AI methods in images, video, and speech means you can also use embeddings for domains such as images, video, and speech in addition to text.

</details>

### 8.3.2 设计使用嵌入向量的解决方案

支持团队与客户合作，但其组织方式是每位成员处理具有类似问题的客户。

<details>
<summary>英文原文</summary>

The support team works with customers but is organized so each member deals with customers with similar problems.

</details>

![图 8.5 此图描述了我们针对客户支持请求的“更好解决方案”，即客户在等待与客服人员交谈时描述自己的问题。LLM利用问题的嵌入表示，将类似问题与已知解决方案进行比对。在用户等待期间，自动化系统可以提供有助于用户自行解决问题的信息，无需支持人员的干预。如](assets/fig-8-5-75da4c6271.png)

*图8.5 此图描述了我们针对客户支持请求的“更好解决方案”，即客户在等待与客服人员交谈时描述自己的问题。LLM利用问题的嵌入表示，将类似问题与已知解决方案进行比对。在用户等待期间，自动化系统可以提供有助于用户自行解决问题的信息，无需支持人员的干预。如果自动方式失败，用户始终可以选择“退出”并与真人交谈。用于生成嵌入的模型不必与引导用户完成解决方案的LLM相同。*

我们完全可以组合使用目前介绍的各种解决方案。例如，图8.5右上角的分析师-客户交互循环可以是两个人在讨论问题，也可以是我们在图8.3中设计的LLM监督验证方案。取决于需要解决的问题不同，既然我们已经有了嵌入表示，就有很多机会扩展这些解决方案。例如，如果分析师记录了客户的愤怒或不满程度，就可以训练一个回归模型，根据客户的嵌入向量预测其愤怒程度。然后，可以将愤怒的客户平均分配给不同的分析师，避免某个人负担过重，或者试图将愤怒的客户安排给那些仍在学习如何帮助客户解决问题的新分析师。

<details>
<summary>英文原文</summary>

It’s entirely possible to combine the solutions we have described so far. For example, the analyst-to-customer interaction loop in the top-right of figure 8.5 could involve two people talking through the problem, or it could be the LLM-supervised validation solution we designed in figure 8.3. Depending on what problems need to be solved, there are many opportunities to extend these solutions now that we have embeddings. For example, if analysts saved information about how angry or upset a customer is, you could train a regression model to predict how angry a customer may be from their embedding. Then, you could distribute the angry customers evenly amongst analysts to avoid someone being overwhelmed or try to route angry customers away from new analysts who are still learning how to help customers solve their problems.

</details>

需要澄清的是，我们并不是说所有客服技术支持系统采用这种方法都会变得更好。目的是向你展示，有多种方法可以利用LLM构建解决方案，以规避其缺陷，例如容易产生幻觉以及无法动态融入新知识。总之，我们提出了两个基本策略：

<details>
<summary>英文原文</summary>

To be clear, we are not saying that all customer service tech support systems will be better if they use this approach. The goal is to show you that there are ways to build solutions with LLMs that work around their shortcomings, such as their tendency to hallucinate and their inability to incorporate new knowledge dynamically. In summary, we present two basic strategies:

</details>

*[未译]* 8.4 Technology presentation matters

读完这个如何设计使用LLM的技术支持系统的例子，你们中有些人可能会觉得难以置信。我们经常听到完全相信LLM技术的人说：“如果让LLM解释其推理过程，用户或分析师就能判断它是否合理，所有与幻觉和错误相关的问题就都解决了。”我们也经常收到来自另一端更持怀疑态度的人的类似请求，他们担心LLM产生的错误，也不理解发生了什么，希望我们创建“可解释的AI”。因此，双方都认为解释将提供建立对技术信任的手段，并相信LLM（或任何机器学习算法）正在正确有效地工作。

在本节中，我们希望讨论一些观点，支持可解释性并非这些问题的解决方案这一看法。可解释性并非帮助捕获错误或使系统更透明和可信的唯一解决方案。不幸的事实是，我们关于LLM如何与人协作的假设往往是错误的，必须仔细评估。事实上，最近的研究表明，当系统采用可解释的AI技术时，人们会错误地仅仅因为存在解释就相信AI是正确的，而不考虑解释的准确性。即使在没有AI支持的情况下用户也能独立完成该任务，并且用户已经被告知AI系统的实际工作原理，情况依然如此[3]。底线是，解释可能会对它们试图推进的目标本身造成危害。

<details>
<summary>英文原文</summary>

Some of you may be incredulous after reading through this example of how we would design a tech support system that uses LLMs. We often hear folks who fully believe in LLM technology say, “If you have the LLM explain its reasoning, the user or analyst can figure out if it makes sense, and all of the problems related to hallucinations and errors will be solved.” We often receive similar requests to create “explainable AI” from those on the more skeptical end of the spectrum who are concerned about the errors LLMs produce and who don’t understand what is happening. Thus, there is a perception on both sides that explanations will provide the means to establish trust in the technology and believe that the LLM (or any machine learning algorithm) is working properly and effectively.

In this section, we want to discuss some points that support the notion that explain-ability is not the solution to these problems. Explainability is not the single solution that will help catch errors or make a system more transparent and trustworthy. The unfortunate truth is that our assumptions about how an LLM will work with people are often wrong and must be carefully evaluated. In fact, recent research has shown that when explainable AI techniques are employed by a system, people erroneously trust the AI to be correct solely based on the fact that an explanation is present, regardless of its accuracy. This is true even when the user could perform the task independently without an AI’s support, and the user has been taught about how the AI systems actually work [3]. The bottom line is that explanations can be harmful to the very goals that they attempt to advance.

</details>

因为这会对要解决的实际目标产生反作用。那么为什么还要做任何形式的可解释AI呢？

<details>
<summary>英文原文</summary>

because it is counterproductive to the actual goals being solved. So why would anyone do any explainable AI of any form?

</details>

从实际角度来看，有两个关键因素使可解释AI有用：

<details>
<summary>英文原文</summary>

Two key things make explainable AI useful from a practical perspective:

</details>

回答问题：可解释性对谁而言？

从问题陈述出发实现可解释AI。例如，一个现实世界的问题陈述可能描述了需要建立对物理或化学过程的科学理解。以此为目标，算法的一种有用解释可能是生成一个能产生答案的方程，而不是直接给出答案。有了这个方程，物理学家或化学家可以检查其逻辑一致性，并将其作为进一步科学探索的起点。

<details>
<summary>英文原文</summary>

Answering the question, explainable to whom?

Reaching explainable AI from the problem statement For example, a real-world problem statement may describe the need to develop a scientific understanding of a physical or chemical process. With this goal, a useful explanation from the algorithm may be to generate an equation that produces the answers rather than producing the answers directly. With the equation, a physicist or chemist can inspect it for logical consistency and use it as a starting point for further scientific exploration.

</details>

在这种情况下，解决方案只有具备深厚专业知识的人才能理解，但恰恰只有这些人需要解释。以方程形式给出的解释直接触及了科学理解的问题，而不仅仅是理解AI算法的内部运作。我们并不清楚AI是如何得出方程本身的，而该方程（希望如此）是一种逻辑一致的形式，能够解释物理或化学过程。

<details>
<summary>英文原文</summary>

In this case, the solution is explainable only to someone with significant exper-tise, but that is the only person who needs the explanation. The explanation in the form of an equation also directly tackles the problem of scientific understanding rather than merely understanding the inner workings of the AI algorithm. We do not have any explanation of how the AI came up with the equation itself, and the equa-tion is (hopefully) a logically consistent form that explains the physical or chemical process.

</details>

这个例子反映了可解释AI最有帮助的一般情况：当它用于帮助一个狭窄且特定的受众（可能是专家用户）执行非常具体的目标时。例如，数据科学家确实常用可解释AI来帮助他们理解为什么某个特定模型会犯特定类型的错误，即使他们使用的工具对非数据科学家受众来说难以理解。

<details>
<summary>英文原文</summary>

This example reflects the general situation in which we find explainable AI the most helpful: when it is used to aid a narrow and specific audience of potentially expert users in performing a very specific goal. For example, it is indeed common for data scientists to use explainable AI to help them figure out why a particular model is making a particular set of errors, even if the tools they use are not com-prehensible to a nondata scientist audience.

</details>

那么，如果可解释性AI并不是建立对AI系统或解决方案信任的答案，那什么才是呢？遗憾的是，目前并没有一种公认的、通用的且经过严格评估的方法来建立对AI的信任。我们这并不新颖的建议是：专注于透明度、用户评估以及所涉及用例的具体细节。

<details>
<summary>英文原文</summary>

So if explainable AI is not a solution for building trust in an AI system or solution, what is? Unfortunately, there is no agreed-upon generic and rigorously evaluated way to build trust in AI. Our unoriginal suggestion is to focus on transparency, user evaluation, and the specifics of the use cases involved.

</details>

### 8.4.1 如何做到

要做到透明吗？透明度可以很简单地告知用户所使用的AI系统：该系统基于哪个模型设计？在高层次上，它是如何修改的？如果系统旨在模仿特定人物（如“接受阿尔伯特·A.I.·爱因斯坦的辅导”）或某类有资质的人员（如“向GPT医生咨询你背上的那颗痣”），那么该人员或具有类似资质的人员是否已同意或认可其有效性？消费者如何验证这些信息？本质上，列举审计员或持怀疑态度的用户可能想知道的这类合理问题及其答案，将使你在提高系统透明度方面远超平均水平。这些信息无需向每个用户详细展示，但提供让用户能够发现这些信息的途径是有益的。这不仅有助于高级用户理解正在发生的事情，也有助于普通用户对给定系统的能力范围建立合理预期。此外，当用户与生成自动响应的系统交互时，告知用户这一点至关重要。试图假装由人类控制、因此应能解决任何合理挑战，与您告知客户能力有限的自动化AI之间，存在着巨大差异。

<details>
<summary>英文原文</summary>

be transparent? Transparency can be as simple as informing users about the AI system that is being used: Which model was it designed with and, at a high level, how was it modified? If the system is meant to mimic a specific person (“Get tutored by Albert A.I. Einstein”) or a type of credentialed person (“Ask Dr. GPT about that mole on your back”), has that person or similarly credentialed person consented to this or approved its efficacy? How can the consumer verify this information? Essentially, enumerating these kinds of reasonable questions and their answers that an auditor or skeptical user might want to know will put you far ahead of the average in making your system more transparent. These do not need to be presented in detail to every user, but having a way for users to discover this information is helpful. It not only helps sophisticated users understand what is happening but also helps set the expectations of users in general about what is and is not possible with a given system. Furthermore, it is essential to inform users when they are interacting with a system that is generating automated responses. There is a big difference between trying to pretend a human is in control and thus should be able to solve any reasonable challenge versus an automated AI that you inform the customer has limited capability.

</details>

### 8.4.2 与用户对齐激励

透明度和系统呈现的一部分涉及激励的一致性。这不仅仅是关于管理实践的一句空话，而是一个实用的建议。回忆第四章，AI算法是贪婪的机器，它们优化的是你要求的内容，而不是你的意图。如果你开始构建一个LLM系统，而系统的激励与你的更广泛目标不一致，你就有可能过度拟合你所要求的内容，而非你和用户共同的需求。

当激励一致时（例如，我们的例子："试用LLM，如果有效，账单立减2美元"），你更可能获得积极的结果。它们还为你提供了更多宣传使用LLM的方式，将LLM作为为客户提供价值的机制，而不是试图取代所有岗位的恶人。展示并讨论企业与客户之间一致的激励，以及你如何使用LLM实现这些目标，就能说清楚需要说明的一切，无需隐藏任何信息。

<details>
<summary>英文原文</summary>

Part of transparency and system presentation involves aligning the incentives involved. This isn’t just a feel-good statement about management practices but a practical unit of advice. Remember from chapter 4 that AI algorithms are greedy machines that optimize for what you ask, not what you intend. If you start building an LLM system where the incentives of the system are not well aligned with your broader goals, you risk overfitting to what you asked, not what both you and your users need. With aligned incentives (e.g., our example of “try out the LLM and get $2 off your bill if it worked”), you are much more likely to have a positive outcome. They also give you more ways to advertise using an LLM as a mechanism for providing value to your customers instead of coming across as the evil people trying to outsource all the jobs. Presenting and discussing the aligned incentives between a business and its customers and how you are using LLMs to achieve those goals describes what needs to be said without any need for hiding the information.

</details>

### 8.4.3 纳入反馈循环

世界并非一成不变。事物总是在变化，今天行之有效的方法明天可能就失效了。

这正是为什么需要对任何自动化AI/ML系统进行定期和持续审计的原因之一：因为它们不会随着经验自动改进或适应。

同时，这也有助于你发现潜在的负面反馈循环，这是你应该提前思考的问题。负面反馈循环并不总是可以预测的。

为了帮助捕捉这些循环，可以思考哪些用户会在新系统中获得最大收益，哪些不会，以及这种模式反复出现时会发生什么。

例如，我们之前提到，语音转文字和文字转语音功能对老年客户或任何有听力或行动障碍的客户很有帮助。如果我们不提供这种选项，可能会逐渐失去这些客户，因为他们每次遇到问题都必须使用操作不便的系统。

假设你是一家依赖家庭套餐获取部分收入的手机公司。那些最初购买家庭套餐的中年客户开始对你的客服系统感到不满，于是他们将自己的整个家庭套餐转移到了另一家服务商，这家服务商投入额外精力确保客服流程准确高效。这样一来，你同时失去了老客户和新客户！

<details>
<summary>英文原文</summary>

The world is not a static place. Things change, and what works today may not work tomorrow. This is one reason why you should have regular and continuous auditing of any automated AI/ML system: because they do not improve or adapt independently with experience. But it will also help you catch potentially negative feedback cycles, something you want to try to think about in advance. Negative feedback cycles are not always possible to predict. To help you catch these, try to think about which users will or won’t find the most benefit with a new system and what happens as that repeats over and over again. For example, we mentioned that speech-to-text and text-to-speech can be helpful for older customers or any hearing or movement-impaired customer. If we did not include such an option, we might alienate those customers over time, because every time they have a problem, they must use a physically difficult system. Imagine you were a cell phone company that relied on family plans for some of your revenue. Your previously middle-aged customers who first bought your family plans are getting frustrated with your support system, so they move their entire family plan over to a new provider who puts in the extra work to ensure that the customer support process is accurate and efficient. Now you’re losing both your older and younger customers at once!

</details>

关键在于要深思熟虑，并训练自己进行这类思想实验。你无法覆盖所有情况，但你会不断进步。定期审计和测试则有助于你发现故障案例、记录它们，并改进你对未来场景和重复问题的思考方式。

<details>
<summary>英文原文</summary>

The point here is to think things through and train yourself to do these thought experiments. You will not catch every case, but you will improve. Regular auditing and testing then help you catch the failure cases, document them, and improve how you think about future situations and repeat problems.

</details>

### 摘要

LLM 会出现错误，因此首先需要确定错误的风险和潜在成本，才能设计出合适的解决方案。如果错误的风险和成本较低，那么使用普通的聊天机器人式 LLM 可能是可行的。可以通过改变用户与系统的交互方式，或将自动化转移到业务流程的其他环节，来控制使用 LLM 的风险。即使采用 RAG 等技术来降低错误风险，让人工参与监督 LLM 也会带来自动化偏差风险。LLM 可以将文本转换为嵌入（embedding），这是一种数值表示，相似的句子会得到相似的数值。这使你能够使用其他机器学习方法，包括聚类和异常检测等经典技术。

尽管 LLM 可以解释其决策，但这些解释往往效果不佳，因为人们会对其产生依赖。相反，应专注于生成满足特定需求或用例的解释，而非泛泛地“需要解释”。设计系统的激励机制，使其与用户的激励相一致。

这样既能避免 LLM 因优化你所问的问题而非你的意图而出错，也是一种向用户沟通和展示 LLM 的好方式。

<details>
<summary>英文原文</summary>

LLMs will have errors, and you first need to determine the risk and potential cost of errors to design an appropriate solution. If the risk and cost of errors are low, you can potentially use a normal chatbot-style LLM. It is possible to control the risk of using an LLM by changing how users interact with the system or shifting automation to a different part of the business process. Including a “human in the loop” to supervise an LLM creates automation bias risk, even when using techniques such as RAG to reduce the risk of errors. LLMs can convert text into embeddings, numeric representations where similar sentences receive similar values. This allows you to use additional machine learning approaches, including classic techniques like clustering and outlier detection.

While LLMs can explain their decisions, their explanations are often ineffective because people become dependent on them. Instead, focus on producing explanations to satisfy a specific need or use case rather than generic “needing to explain.”

Design your system’s incentives to align with your user’s incentives. This is both a good way to avoid mistakes from an LLM optimizing for what you asked instead of what you intended and a good way to communicate and present your LLM to users.

</details>



---

<a id="ch9"></a>

## 第 9 章: 构建与使用 LLM 的伦理

9 构建与使用大语言模型的伦理

<details>
<summary>英文原文</summary>

9 Ethics of building and using LLMs

</details>

### 本章涵盖

- LLM 执行多任务的能力也带来意外风险

- LLM 与人类价值观的错位问题

- LLM 数据使用对内容创作及未来模型构建的影响

<details>
<summary>英文原文</summary>

- How LLMs’ abilities to perform many tasks also create unanticipated risk
- The question of LLMs’ misalignment with human values
- The implications of LLMs’ data use on content creation and building future models

</details>

尽管讨论伦理可能让一些人想起大学入门课程中枯燥的阅读材料，但在实施可能影响人类的算法时，仍有一些关键的考虑因素。鉴于LLM的使用及其能力范围快速增长，我们必须关注并处理许多不断演变的问题。如果你不了解这些问题，就无法在解决它们时发声。

探索构建和使用LLM的伦理是一个极其复杂的话题，难以完全呈现。因此，本章将介绍我们认为在构建LLM过程中常见的关注点及相关伦理问题。在本章中，我们将引用一些资料来完善这一讨论，以便你愿意时可以进一步探究。

<details>
<summary>英文原文</summary>

Although the discussion of ethics may remind some of you of the dull readings from an entry-level college class, there are critical considerations when implementing algorithms that have the potential to affect humanity. Given the rapid growth in LLM use and their scope of capabilities, we must be aware of and attend to many evolving concerns. If you are unaware of these concerns, you will have no voice in their resolution.

Exploring the ethics of building and using LLMs is an incredibly complex topic that is challenging to represent completely. As a result, this chapter will present what we believe to be common concerns about building LLMs and the related ethical questions. Throughout the chapter, we’ll reference materials that round out this conversation so you can investigate further if you wish.

</details>

我们将讨论三个主要话题：

<details>
<summary>英文原文</summary>

We’ll cover three main topics:

</details>

人们为什么要构建LLM？它们提供了哪些此前不存在的能力？

一些机器学习专家认为，在未来的迭代中，LLM将导致人类灭绝，因为它们会通过自动化取代人类。即使我们不同意他们的观点，也值得理解这种恐惧的根源。

LLM所需的训练数据量是惊人的。构建LLM的公司（如OpenAI和Anthropic）是如何获取所有这些数据的？由于数据的收集和使用方式，会产生哪些可能涉及道德、法律和财务影响的伦理问题？

<details>
<summary>英文原文</summary>

Why do people want to construct LLMs, and what do they provide that didn’t exist before?

Some experts in machine learning believe that in future iterations, LLMs will lead to the extinction of the human race because they will automate us out of existence. Even if we do not agree with them, it is worth understanding the basis for this fear.

The amount of training data needed for LLMs is monstrous. How do companies that build LLMs, such as OpenAI and Anthropic, source all that data? What ethical concerns arise that may have moral, legal, and financial implications due to how that data is collected and used?

</details>

这些考量在伦理和法律层面都相当复杂。我们的目标并非评判创建这些模型是否合乎伦理，而是概述每项讨论中的主要考量。我们希望这能帮助您更广泛地思考大语言模型的影响、后果与风险。我们看到许多围绕大语言模型使用的高关注度且伦理层面复杂的问题，而许多从业者此前并未真正深入探讨过这一主题。尽管如此，我们认为考虑构建大语言模型时的伦理问题至关重要，并将在此章介绍一些关键关注点。

讨论我们如何使用大语言模型与如何构建大语言模型时，同样需要许多考量，因此我们将这一讨论分为两部分。第一部分聚焦构建大语言模型的一般伦理，后半部分则涵盖使用大语言模型的伦理影响。

最后，我们将避免将这些争论归因于特定个人或群体。我们的目标是防止偏见，避免在讨论中点名批评任何人。问题本身才是重点。

<details>
<summary>英文原文</summary>

These are complicated considerations on both ethical and legal fronts. Our goal is not to tell you whether the creation of these models is ethical or nonethical but rather to outline primary considerations under each discussion. We hope this helps you consider LLMs’ implications, consequences, and risks on a broader scale. We see many high-profile, ethically sophisticated questions around LLM use, and many practitioners have not had to grapple meaningfully with this subject. Nevertheless, we believe that it is crucial to consider the ethical questions around building LLMs, and we will introduce you to some of the critical concerns to consider in this chapter. There are just as many considerations necessary when discussing how we use LLMs versus how we build LLMs, so we’ve divided this conversation into two sections. First, we focus on the ethics of building LLMs in general, while the latter section will cover the ethical implications of LLM use.

Last, we will avoid ascribing these arguments to specific individuals or groups. Our goal is to prevent bias and avoid “calling out” anyone in particular in this discussion. The concerns are what’s important.

</details>

### 9.1 我们究竟为何要构建LLM？

在讨论开发LLM的伦理影响之前，值得思考的是，我们构建LLM试图实现什么，以及为何要达成这些目标。与所有软件工程一样，构建LLM通常旨在减少或消除某些任务中的人工劳动。一些经济学家可能会告诉你，这就是生活水平普遍提高的方式。随着技术进步，需要从事体力密集型任务的人越来越少，因此他们有更多时间用于探索、创造以及其他需要高级认知的功能。

就LLM而言，一个常见目标是提高算法的效率，用于诸如自动语言翻译、语音转文字转录、读取图像和印刷文档中的文本（例如光学字符识别）、索引和检索信息（简称为“搜索”或更广泛的信息检索）等应用，以及更多。其他人则出于纯科学原因对LLM感兴趣，例如研究计算语言学的方法，或用于创意应用，如图像、音乐或视频的生成。此外，还有人可能希望增加影响我们生活的技术的可访问性和透明度，或者仅仅是因为LLM吸引了他们的注意力，并展现出惊人的新能力。

对一些人来说，LLM能实现的多种功能本身就是构建它们的内在动力。AI和ML算法已经执行我们列出的所有任务一段时间了；例如，机器翻译已有数十年历史。LLM的不同之处在于，它们似乎能够用一个模型和算法完成所有任务。在LLM出现之前，工程师会将翻译和转录等任务分别实现在各自独立的系统中，以满足特定需求。当今最大的LLM可以在一定程度上完成这些任务以及更多。通常，它们似乎能够完成看似无穷无尽的任务。与此同时，其他人则担心LLM，因为他们认为由于其广泛的能力，LLM将接手那些曾被认为只有人类才能完成的探索和创造任务，从而剥夺人类的工作、动力和活动。

<details>
<summary>英文原文</summary>

Before we talk about the ethical ramifications of developing LLMs, it’s worth thinking about what it is we are trying to accomplish by building LLMs and why we want to achieve those things. Like all software engineering, building LLMs commonly aims to reduce or eliminate human labor from some tasks. Some economists might tell you that this is how standards of living generally increase. As technology advances, fewer people need to perform manual, labor-intensive tasks, and thus, they have more time for discovery, creation, and other functions that use high-level cognition. In the case of LLMs, a common goal is increasing the efficiency of algorithms for applications such as automated language translation, speech-to-text transcription, reading text contained in images and printed documents in applications such as Optical Character Recognition, indexing, and retrieving information, known simply as “search” or, more broadly, as information retrieval, and more. Others are interested in LLMs for purely scientific reasons, such as studying methods in computational linguistics, or creative applications, such as generating images, music, or videos. Furthermore, others may seek to increase access to and transparency of technology that affects our lives, or it may be just because LLMs have grabbed their attention and present fantastic new capabilities.

For some, the variety of things LLMs can achieve is an intrinsic motivation for wanting to build them. AI and ML algorithms have been doing all the tasks we listed for some time; for example, machine translation is decades old. Part of what makes LLMs different is that they seem capable of doing everything with one model and algorithm. Before the advent of LLMs, engineers would implement tasks like translation and transcription in separate systems designed to meet those needs individually. The largest LLMs today can, to some degree, do each of these things and more. Often, it seems they can complete tasks of seemingly endless scope. At the same time, others fear LLMs because due to their breadth of capability, they believe they will steal work, motivation, and activity from humans by taking on tasks requiring discovery and creation, previously thought to be reserved for humans only.

</details>

### 9.1.1 LLM包揽一切的利与弊

由于LLM可以通过单一模型执行多种不同任务，我们可以将其描述为一种“万能应用”：一个AI助力的全能平台。从可用性角度来看，LLM近乎通用的能力带来了诸多益处，例如它们相对擅长将复杂任务分解为一系列步骤，或者能够生成独特的解释来填补特定的知识空白。此外，聊天式界面似乎很受用户欢迎，即使有其他与LLM交互的方式。聊天受欢迎可能源于其普遍的可访问性：我们经常与人聊天。电话使用广泛，而短信、Slack、Teams、即时消息和电子邮件让人潜意识中就知道如何使用各种基于聊天的界面。因此，通过聊天界面与AI交互已成为一种诱人且简单的方式，只需很少的培训即可提升采用率。基于聊天应用的广泛经验还具有民主化效果：用户只需学习一次，就能追求许多不同目标。这类系统的主要缺点是，虽然它可以用于一切，但并不意味着我们应当用它来应对一切。当算法能被用于许多不同且可能意想不到的任务时，我们没有时间测试所有可能的用途。由于LLM潜在应用的广度，在验证模型安全操作的范围与其可能尝试但具有潜在危险或危害的范围之间会存在差距。例如，当前的LLM模型可以对种族或性别进行抽象评估，即使这些评估可能包含有害的负面偏见。虽然我们可以针对特定有害偏见实例开发测试和防御措施，但这些措施通常范围狭窄且高度特定。例如，假设我们要求一个图像生成模型生成一张商务会议的图像。不幸的结果是，图像中所有人常常是男性和白人。自然，我们希望模型超越这些刻板印象。然而，识别并修复像这样的特定上下文偏见问题并不会影响模型在现实世界部署中因不同且未预期提示而产生危害的可能性。这些努力最多只能展示LLM如何失败，但解决危害需要理解可能发生的失败，例如由于训练数据中的偏见。同时，我们必须理解人们将如何使用LLM，以及这些使用是否可能因LLM生成输出的方式而导致意外伤害。这可能意味着明确不为某个预期用例使用LLM，因为缺乏对其潜在危害的缓解措施。最近关于已部署LLM现实危害的研究发现，OpenAI的ChatGPT和Google的Gemini等LLM对使用非裔美国英语方言者的隐性偏见比1920年代对美国白人的陈旧负面刻板印象更严重[1]。另一项研究考虑了医生就不同种族的医疗最佳实践和治疗方案咨询LLM的用例，发现模型频繁推荐已被驳斥的基于种族、源于优生学“科学”的医疗做法[2]。不幸的是，我们在那些在现有显性偏见基准上得分相当不错的模型中继续看到这些问题。隐性偏见的普遍性表明这些基准不足以评估潜在危害，并强调需要根据使用情况考虑LLM可能造成的危害。换句话说，更重要的不是将AI部署的危害归因于模型是否包含种族偏见的一般概念，而是将其视为特定提议用例和应用的直接结果。目前，我们不知道如何设计一个能够在一个系统中完成这么多任务同时防御善意个人的意外滥用和危害的算法。因此，从开发者的角度来看，进行跨广泛群体和场景的彻底用户研究以识别意外风险，并包含监控和日志记录以补救任何后期发现的风险，变得至关重要。无论我们试图防止有害的种族刻板印象还是推广已被驳斥的医疗做法，当前限制LLM滥用的方法是列举已知问题，并采用微调方法（如RLHF）强制模型在已知问题上表现更好。不幸的是，由于LLM能力的潜在广度，未知问题的集合是无限的，因此任何测试制度都将是不完整的。

<details>
<summary>英文原文</summary>

Given that an LLM can perform many different tasks via a single model, you could describe it as a kind of “everything app”: your one-stop shop for AI-powered assistance. From a usability perspective, many benefits have emerged from the near-universal capability of LLMs, such as their relative aptitude for decomposing complex tasks into a series of steps or their ability to generate unique explanations to fill specific knowledge gaps. Additionally, the chat-style interface seems very popular with users, even if other ways of working with LLMs are available. The popularity of chat may be due to its general accessibility: you chat with people constantly. Experience with phone calls is widespread, and with texts, Slack, Teams, instant messaging, and email, people implicitly know how to use various chat-based interfaces. As a result, interacting with an AI via a chat-based interface has become an inviting and easy way to increase adoption with little training. The widespread experience with chat-based applications also has a democratizing effect: users only need to learn something once to help them pursue many different goals. The primary disadvantage of such a system is that although it can be used for everything, that doesn’t mean we should use it for everything. When you have an algorithm that people can use for many different and potentially unexpected tasks, you do not have the time to test every possible use. Due to the breadth of potential applications of LLMs, there will be a gap between validating what the model does safely and what it can attempt to do but that could be potentially dangerous or harmful. For example, current LLM models can perform abstract evaluations of race or gender, even though these evaluations may contain harmful negative bias. While we can develop tests and defenses for specific instances of harmful bias, these are likely to be narrow in scope and highly specific. For example, suppose we ask for an image generation model to generate an image of a business meeting. The unfortunate result is that all people in that image will often be male and white. Naturally, we wish the model to transcend these stereotypes. However, identifying and fixing specific contextual bias concerns like this will not affect whether a model would cause harm when deployed in the real world and prompted in different, unanticipated ways. At best, these exercises exemplify how an LLM can fail, but addressing harm requires understanding the potential failures that can happen, for example, due to bias in the training data. Simultaneously, we must understand how people will use LLMs and whether those uses may lead to unintended harm due to how the LLM generates output. This may mean expressly not using an LLM for an intended use case due to the lack of mitigations for potential harms they may cause. Recent research on the real-world harms of deployed LLMs found that the implicit bias in LLMs like OpenAI’s ChatGPT and Google’s Gemini against people who use African American vernacular English was worse than the archaic negative stereotypes measured among white Americans in the 1920s [1]. Another study considered the use case of a doctor consulting an LLM for information on medical best practices and treatment options for people of different races and found that the models frequ-ently recommended debunked race-based medical practices grounded in eugenicist “science” [2]. Unfortunately, we continue to see these problems in models that score quite well on existing explicit bias benchmarks. The prevalence of latent bias suggests these benchmarks aren’t sufficient in evaluating potential harms and emphasizes the need to consider the harm an LLM can cause based on its use. In other words, it is more important to view harm due to AI deployment as a direct result of the specific proposed use cases and application, not as something we can ascribe to a general notion of whether a model contains racial bias. Today, we do not know how to design an algorithm capable of doing so many tasks in one system while simultaneously providing defense against accidental misuse and harm by well-meaning individuals. So it becomes critical from a developer’s perspective to do thorough user studies across a wide range of groups and settings to identify the unintended risks and to include monitoring and logging to remediate any late-identified risks. Whether we are attempting to prevent harmful racial stere-otypes or the advocacy for debunked medical practices, the current approaches to constraining the misuse of LLMs are to enumerate what we know about potential problems and employ fine-tuning methods, such as RLHF, to force the model to behave better on known problems. The unfortunate side of this is that due to the potential breadth of LLM capabilities, the set of unknown problems is infinite, and as such, any testing regime will be incomplete.

</details>

注意：部署后监控的重要性并非新鲜事。

例如，FDA通过MedWatch系统实施这一做法已有多年。该系统允许公众和医疗专业人员报告任何药物或医疗器械的不良事件，以便FDA监控异常情况。

<details>
<summary>英文原文</summary>

NOTE The importance of postdeployment monitoring is not new. For example, the FDA has practiced this for many years with the MedWatch system. This system allows the public and medical professionals to report any adverse events with a drug or medical device so that the FDA can monitor for anything unusual.

</details>

9.1.2 我们是否要让所有人类工作自动化？

<details>
<summary>英文原文</summary>

9.1.2 Do we want to automate all human work?

</details>

正如我们在引言中提到的，一些经济学家可能会认为自动化能让劳动力专注于新的工作。这一论点基于这样一种观点：自动化的进步擅长消除大多数人不愿意做的工作。农业是艰苦的，稀土金属开采是艰苦的，组装汽车、玩具和包装也是艰苦的。这些是艰苦且损害身体的劳动，通常伴随着有限的智力刺激。像农业这样的重体力劳动，如今所需的劳动力比1950年减少了74% [3]，毫无疑问，也比中世纪时期减少了无数倍。

与大语言模型的不同之处在于，它们可能自动化某些类型的白领知识工作。文案撰写[4]、视觉艺术[5]、平面设计[6]和银行业[7]只是生成式AI冲击的几个领域。

那些担心大语言模型对经济影响的人认为我们会因自动化而失去工作，但我们提醒，这并不像常说的那样非黑即白。机构和消费者的需求可能会推动这些白领岗位的保留和持续扩张。我们应警惕忽视关于技术进步如何改变工作岗位的经济学研究历史。相反，我们必须应对一个更重大的担忧：获取高质量训练数据。我们相信这将推动未来新岗位的产生，强调人类创造力和能力的重要性，即使它目前创造的岗位还不是许多人期望的那种理想白领工作。

<details>
<summary>英文原文</summary>

As we mentioned in the introduction, some economists might argue that automation allows the labor pool to focus on new work. This argument hinges on the idea that advances in automation have been good at eliminating work that most people don’t want to do. Farming is hard, mining rare earth metals is hard, and assembling cars, toys, and packages is hard. These are difficult labor and body-destroying jobs often coupled with limited intellectual stimulation. Heavy labor like farming requires 74% fewer laborers today than it did in 1950 [3] and, undoubtedly, many times fewer than it did back in the medieval era.

The difference with LLMs is the potential to automate away certain types of white-collar knowledge work. Copywriting [4], visual arts [5], graphic design [6], and banking [7] are just a few of the fields disrupted by generative AI. Those concerned about LLMs’ effect on the economy suggest that we will lose jobs to automation, which we caution is not as clear-cut as often portrayed. Institutional and consumer desires may push for retention and continued expansion of these types of white-collar jobs. We should be wary of ignoring a history of economic study about how jobs change as technology advances. Instead, we must address a more significant concern: obtaining high-quality training data. We believe this will drive new jobs in the future, emphasizing the importance of human creativity and ability, even if the current jobs it creates are not yet the desirable kind of white-collar work that many would prefer.

</details>

### 关于“显而易见”结果的反例

有人认为，LLM显然会对经济的某些领域产生好坏不一的影响。银行柜员的工作是一个常被用来反对LLM的著名例子，自20世纪60年代自动取款机（ATM）发明以来，该工作发生了显著变化。显然，ATM自动化了许多银行柜员的任务。

但ATM的例子并非如此简单。在ATM发明后的几十年里，柜员的工作岗位数量反而增加了，从1970年到2010年，即使ATM变得更加普及，柜员岗位数量仍翻了一番，达到约60万个[8]。回顾对ATM对就业影响的史研究，人们认识到许多因素导致了失业，包括增长率的变化和工作性质的变化。失业不仅源于ATM技术，还源于业务其他方面的多轮技术创新、银行应对变化方式的差异、放松管制以及银行业竞争的加剧和整合[9]。因此，尽管ATM在银行柜员的工作上可以说更好更便宜，但机构、客户和期望的性质阻止了工作岗位的立即下降，并使情况比通常宣传的要复杂得多。

ATM的例子并非唯一；技术可能导致、但并非总是导致因自动化而失业。例如，机器翻译在21世纪初和2016年两次取得了巨大进步。然而，翻译工作岗位在每个时期都有所增加，并且至今仍在增长[10]。关键的观察是，当翻译人员将自动化工具融入工作流程时，翻译工作的岗位池并不会缩小。相反，我们看到完成的翻译工作量以及翻译服务需求都在增长，因为需要翻译的材料量持续增加。有人认为，类似的需求也会出现在创意艺术家和作家身上[11]。根据这一论点，虽然艺术创作和知识工作的方式将发生变化，但市场将继续增长，需求将继续上升，这能够利用自动化工具引入带来的新增劳动力供应。因此，当我们识别出可能被LLM自动化或加速的工作领域时，我们还必须确定效率和质量的提高是否会推动更多需求。

尽管如此，其他人会认为LLM从根本上不同于以往的一切。因此，我们无法使用以往理解技术对经济潜在影响的方法来预测未来。尽管考虑到围绕LLM的各种炒作，这种观点可能很诱人，但我们怀疑这是否是一个过于宽泛、无法证明真伪的论断。虽然我们在制定法规时确实应考虑这种可能性和因素（这反过来又在技术如何影响就业方面起着重要作用），但值得注意的是，据估计美国60%的工作是现代发明，以前并不存在[12]。

<details>
<summary>英文原文</summary>

Some argue that it is obvious that LLMs will affect some sectors of the economy for better or worse. The bank teller’s job is a famous example often used to argue against LLMs. The job of bank tellers has changed significantly since the invention of the Automatic Teller Machine (ATM) in the 1960s. Clearly, the ATM automated many of the bank teller’s tasks. But the ATM example is not that simple. The number of teller jobs increased for decades after the invention of the ATM, doubling to ≈600, 000 between 1970 and 2010 even as the ATM became more widely available [8]. Looking to historical studies of the ATM’s effect on jobs, it was recognized that many factors contributed to job loss, including changes in the growth rate and the nature of the job. Job loss came as a result of not just ATM technology but multiple rounds of technology innovation in other parts of the business, differences in how banks responded to the change, deregulation, and increased competition and consolidation in the banking industry [9]. So even though the ATM was arguably better and cheaper at the bank teller’s job, the nature of institutions, customers, and expectations prevented any immediate decline in jobs and made the situation far more complex than is often advertised. The ATM example is not unique; technology can, but does not always, lead to job losses due to automation. For example, machine translation improved dramatically in the early 2000s and again in 2016. Still, jobs for translation work increased within each period and continue to grow today [10]. The critical observation is that the job pool for translation doesn’t shrink when translators incorporate automated tools into their workflow. Instead, we saw a growth in the volume of translation work completed and an increase in demand for translation services as the amount of material requiring translation continues to grow. Some argue that similar demand will materialize for creative artists and writers [11]. According to this argument, while the means of producing art and performing knowledge work will change, the market will continue to grow, and demand will continue to rise in a way that can take advantage of the new supply of labor resulting from the introduction of automated tools. Thus, when we identify an area of work that may be automated or accelerated by LLMs, we must also determine whether the increased efficiency and quality could drive more demand. Still, others will argue that LLMs fundamentally differ from everything that has ever happened. Thus, we cannot use prior methods of understanding technology’s potential effects on the economy to predict the future. Although possible and temp-ting to believe, given all the hype around LLMs, we are skeptical as to whether this is an overly broad statement that no one can prove false or true. Although we should indeed consider such possibilities and factors when making regulations (which, in turn, play a significant part in how jobs evolve with technology), it is also notewor-thy that an estimated 60% of all US jobs are modern inventions that did not exist previously [12].

</details>

### 训练数据的生成式考量

由于场景中存在一种诡异的二元性，AI对创意表达的影响令人深省。在互联网上发布内容的作家和艺术家们，他们的作品在很大程度上正在滋养那些似乎要消灭他们工作的模型。LLM研究者提出的伦理论点是，他们应该能够自由地使用这些创作者的内容作为训练数据。这一论点可能导致皮洛士式胜利，最终反而成为AI的败局。如果AI取代了创意工作者的劳动，LLM开发者将会发现，由于缺乏人类生成的内容，以及训练LLM所需数据量的指数级增长超过了用户生成内容的线性增长，他们再也无法改进模型。更重要的是，那些创作内容的人将不再有工作或动力仅仅为了让LLM吞噬而创作内容。

这种负面循环将同时影响LLM和内容创作者，即便这只是一种感知风险而非真正的担忧。数据采集以训练LLM对于数千个依赖用户生成内容和广告收入的网站来说是一个重大问题。这些网站为LLM提供了宝贵的训练数据，而LLM的构建者需要海量训练数据，却对广告收入毫无贡献。

例如，Stack Exchange是一组网站，用户可以发布问题，其他用户回答，并根据优质回答获得声誉评分。Stack Exchange旗下的Stack Overflow，对于寻求解决编程问题的程序员而言无疑是天赐之物。Stack Exchange还拥有许多其他多样化的用户社区，服务于系统管理员、数学学生和桌游爱好者等群体。

<details>
<summary>英文原文</summary>

AI’s effect on creative expression is poignant due to the situation’s per-verse duality. Much of the work of writers and artists who post their content on the internet is fueling models that are seemingly out to eliminate their jobs. The ethical argument made by LLM researchers is that they should be able to freely use content from these creators as training data. This argument may lead to a pyrrhic victory and, ultimately, an undoing for AI. If AI replaces the work of creatives, LLM developers will find that they can no longer improve their models due to a lack of human-generated content and the exponential size increases in the data needed to train LLMs exceeding the linear growth in user-generated content. More importantly, the folks who create that content can no longer be employed or motivated to create content merely to have it slurped up by an LLM. This negative cycle will affect both LLMs and content creators, even if it is only a perceived risk and not a genuine concern. Data harvesting to train LLMs is a significant concern for thousands of websites that rely on user-generated content and advertising revenue from those who consume that content. These sites provide precious training data for LLMs, whose builders require massive collections of training data but do nothing to contribute to advertising revenue. For example, Stack Exchange is a collection of websites where users can post questions, have other users answer them, and receive a reputation rating for good answers. One of Stack Exchange’s websites, Stack Overflow, is a godsend to program-mers looking for help solving coding problems. Stack Exchange also hosts many other diverse user communities catering to system administrators, math students, and tabletop gaming enthusiasts.

</details>

随着LLM的出现，Stack Exchange迅速调整其商业模式，试图要求LLM创作者付费以维持其财务未来[13]。即使训练LLM的公司与托管内容的网站之间已有协议，对用户生成内容进行更直接的商业化可能仍令用户反感。Stack Overflow就经历了这一点，人们开始从该平台删除他们有帮助的回答，以抗议Stack Overflow将其免费劳动成果出售给LLM创作者[14]。

这个例子反映了搜索引擎长期以来将索引的应用程序和网站的功能整合到其主界面的历史。例如，现在可以直接在谷歌搜索界面内搜索和比较机票价格。这种能力将流量从提供相同服务的成熟旅行网站引流[15]，并减少了对构建这些服务的公司的服务和收入的需求。当创意作品成为训练数据时，基于这些作品训练的LLM与原始创作者之间可能存在类似的关系。

很明显，由于LLM兴起而面临的问题与我们在以往自动化时期看到的问题相似但不完全相同。那么问题就变成了：与LLM部署相关的差异是否足以导致不同的、更负面的结果。结果对我们来说并不明显，主要是由于LLM的广泛规模、可访问性和适用性。LLM开发者应主动理解和减轻潜在危害，例如与可能受影响的领域预先协商数据使用和社区建设。我们将在本章最后一节讨论训练数据及其来源的其他方面。

<details>
<summary>英文原文</summary>

With the advent of LLMs, Stack Exchange was quick to change its business model and attempted to require payment from LLM creators to sustain its financial future [13]. Even with agreements between companies training LLMs and the websites hosting content in place, more direct commercialization of user-generated content may not be palatable to users. Stack Overflow experienced this as people began to delete their helpful answers from the platform in protest of Stack Overflow selling the results of their free labor to LLM creators [14].

This example mirrors a long history of search engines integrating the capabilities of the applications and websites they index into their primary interface. For example, it is now possible to search for and compare prices for airline tickets directly from within the Google search interface. This capability drives traffic away from established travel sites that provide the same service [15] and reduces the demand for the services and revenue of the companies that built those services. A potentially similar relationship exists between the LLMs trained on creative works and the original producers of that work when it becomes training data.

It seems clear that the problems we are dealing with due to the rise of LLMs are similar, but not identical, to the problems we’ve seen in previous periods of automation. The question then becomes whether the differences related to LLM deployment are sufficiently significant to result in a different, more negative outcome. The outcomes are not apparent to us, primarily due to the broad scale, accessibility, and applicability of LLMs. It is up to LLM developers to take the initiative to under-stand and mitigate potential harms, like prenegotiating data usage and community building with the likely-to-be-affected fields. We will discuss other facets of training data and its sourcing in the last section of this chapter.

</details>

### 9.2 LLM是否构成存在风险？

有些人认为LLM本身就是危险的。如果你不熟悉这个论点，可能会觉得训练一个强大的LLM模型会导致重大的现实危害，比如消除隐私、终结者机器人以及对我们所知的人类生存的威胁，这听起来很荒谬。然而，许多人对此感到担忧，包括AI领域的领军人物，如Geoffrey Hinton[16]和Yoshua Bengio[17]。Hinton和Bengio是深度学习领域最受尊敬的研究人员之一，他们在神经网络技术在AI中的生存、复兴和主导地位方面做出了重大贡献。

我们认为AI并不构成现实的威胁。然而，严肃且备受尊敬的人士正在提出这些主张，因此理解他们的论点并解释为什么我们认为这些担忧不如解决对工作性质的更直接影响以及确保公平可持续的数据许可和创作者补偿那么重要。

在本节中，我们将聚焦于一个普遍论点：AI可能在广泛意义上成为对人类的风险，因为我们可能失去对LLM的控制，并且LLM可能做出对人类有害的决定。这一观点源于两个被推向极端的想法：

<details>
<summary>英文原文</summary>

Some believe that LLMs are, in themselves, dangerous. If you are unfamiliar with the argument, it may sound absurd that training a powerful LLM model could result in significant real-world harms such as eliminating privacy, terminator robots, and threats to human existence as we know it. Yet many are concerned about these risks, including leaders in the field of AI like Geoffrey Hinton [16] and Yoshua Bengio [17]. Hinton and Benigo are two of the most well-regarded researchers in deep learning who share significant credit for the survival, revival, and dominance of neural network techniques in AI.

We believe AI does not present a realistic threat. However, serious and well-respected people are making these claims, so it is important to understand their arguments and explain why we believe these concerns are less significant than the need to address more immediate effects on the nature of work and ensure equitable and sustainable data licensing and compensation for creators. In this section, we’ll focus on the general argument that AI could, broadly, become a risk to humanity because we could lose control over the LLMs, and LLMs might make decisions detrimental to humans. This notion stems from two ideas taken to their extremes:

</details>

LLM可以利用工具构建新的LLM从而自我改进的观点，以及LLM如果目标与人类需求不一致，可能最终为了自身利益而采取危害人类行为的观点。我们在本书中已经间接提到了第一个关于自我改进的观点。我们讨论过，设计LLM涉及开发数据收集工具，以及编写利用这些数据训练LLM的代码。我们可以假设，如果LLM能够直接使用工具进行数据收集和训练，无需人工干预，那么一个LLM理论上可以训练另一个LLM。

支持这一推理所需的认知跳跃在于，LLM要足够聪明，能够构建出更好的LLM。要接受这一点，我们必须假设这个新的LLM能够创造出更优的LLM2，进而相信这种改进循环可以无限重复，直到LLM∞模型比任何可能存在的人都更智能，并且能够预测、颠覆或对抗任何可能中断这个循环的人类行为。这一跳跃颇具挑战性，因为根据当今技术的观察，我们几乎没有证据表明这种情况可能发生。

第二个观点常被称为“对齐问题”：与人类需求不一致的LLM可能会选择对人类有害的目标和结果。这个观点是合理的，因为正如第4章所讨论的，创建仅衡量预期目标的指标是很困难的。然而，这一思路所需的巨大跳跃在于，LLM将拥有直接与物理世界交互的能力和资源，如果不加阻止，可能导致大规模伤害。

有些人将这两种观点结合起来，认为LLM可能拥有与人类不一致的目标。他们认为，在某个时刻，LLM会意识到为了实现目标，它需要变得更聪明并提升自己。在此过程中，它会从人类手中夺取资源，或者通过其改进后的智能迫使人类服从，以帮助其实现目标。我们在图9.1中概述了这一观点。

这一论点的一个重要方面是，以自我保护为目标的LLM判定人类正在毁灭地球。由于LLM存在于地球上并希望继续存在，它判定毁灭人类是维持自我保护的最佳方式。

我们认为，对人类毁灭的担忧是没有充分依据的。尽管如此，包括拥有计算机科学博士学位且专攻深度学习的人在内的许多人仍对此情景感到担忧。这种“LLM毁灭人类”概念的主要问题在于它依赖于不可证伪的逻辑。不可证伪的逻辑暗示事情会发生，而几乎任何人都无法证明它们不会发生。在这种情况下，证明LLM不会毁灭人类是困难的。

<details>
<summary>英文原文</summary>

The idea that an LLM can use tools to build new LLMs and thus potentially self-improve The idea that an LLM with a goal not aligned with human needs may ultimately decide to take actions detrimental to human life in the interest of its own goals We have touched on this first idea about self-improvement tangentially throughout this book. We have discussed the fact that designing LLMs involves developing tools for data collection and creating the code to train an LLM using that data. One might hypothesize that if an LLM can use tools for data collection and training directly, without human intervention, an LLM could hypothetically train another LLM. The cognitive leap required to support this line of reasoning is that an LLM will be smart enough to build a better LLM. For us to accept this, we must assume that this new LLM will then be able to create an even better LLM2 and, further, believe that this improvement cycle could repeat forever until the LLM∞model will be more intelligent than any person who could ever exist and essentially be able to predict, subvert, or counteract any possible human action that might interrupt this cycle. This leap is challenging because we have little evidence that something like this is likely, based on what we observe in today’s technology.

The second idea, often referred to as the “alignment problem,” is that LLMs misaligned with human needs may choose goals and outcomes that are detrimental to humans. This idea is reasonable because, as discussed in chapter 4, creating a metric that measures only your intended goals is challenging. However, the extraordinary leap required for this line of thinking is that LLMs will have the ability and resources to interact with the world directly and physically, which could result in mass harm if not stopped.

Some combine these two ideas to argue that an LLM may have goals misaligned with humanity. They believe there will be a point at which an LLM realizes it needs to become more intelligent and improve itself to achieve its goals. As it does so, it takes resources away from humans or, via its improved intelligence, forces humans into subservience to help it achieve its goals. We outline this idea in figure 9.1. An essential aspect of this argument is that the LLM, with a goal of self-preservation, determines that humans are destroying the planet. Since the LLM exists on earth and wants to continue doing so, it determines that destroying humans would be the best means of maintaining self-preservation.

We do not think the potential for humanity’s destruction is a well-founded concern. Still, many people, including those with doctorates in computer science and who specialize in deep learning, are concerned about this scenario. The main problem with this “LLM-destroys-humanity” concept is that it relies on unfalsifiable logic. Unfalsifiable logic suggests that things will happen, and it is nearly impossible for anyone to prove that they will not. In this case, proving that LLMs won’t destroy humanity is challenging.

</details>

![图 9.1 在对齐问题中，那些认为LLM对人类构成生存风险的人通常会提出两类假设性担忧。上方的路径展示了一个直接对齐问题，即AI的目标解决方案直接伤害人类。下方的路径展示了一个间接对齐问题，即AI为实现最终目标创建了子目标。即使目标——比如解决一道高难](assets/fig-9-1-5c597bf53b.png)

*图9.1 在对齐问题中，那些认为LLM对人类构成生存风险的人通常会提出两类假设性担忧。上方的路径展示了一个直接对齐问题，即AI的目标解决方案直接伤害人类。下方的路径展示了一个间接对齐问题，即AI为实现最终目标创建了子目标。即使目标——比如解决一道高难度数学题——达成了，这个LLM也会以牺牲人类为代价来完成。在中间步骤中，LLM认为它需要比人类可共享的更多的地球资源来解决问题。*

茶壶与不可证伪的陈述 在讨论LLMs可能毁灭人类这类抽象风险时，要求对方做出可证伪的陈述至关重要。一个著名的例子是伯特兰·罗素的“茶壶”思想实验。这个想法很简单：有人告诉你太空中存在一个茶壶，它太小太远，无法被探测到。这个前提本身是不可证伪的；我可以花几个世纪扫描宇宙寻找茶壶，但即使找不到，我也无法证明它不存在。唯一的可能性是，我最终找到了一个茶壶，并确认它存在于太空中。

否则，我永远无法证明茶壶的存在是个谎言。因此，在讨论抽象风险时，不可证伪的陈述会成为一个认知死胡同。反驳一个无人能证明其错误的陈述是不可能的。同时，这些陈述对推进对话、得出有意义的见解或结论毫无帮助。相反，基于可被承认和解决的现实且实际的关切来提出论点，对于理解问题更有价值。

<details>
<summary>英文原文</summary>

Teapots and unfalsifiable statements Demanding that someone make a falsifiable statement is essential in discussing abstract risks like LLMs’ potential to destroy humanity. A famous example is Ber-trand Russell’s “teapot” thought experiment. The idea is simple: someone tells you that a teapot exists in space, too small and too far away to be detected. The premise itself is unfalsifiable; I can scan the universe for centuries looking for a teapot, but even though I can’t find it, I cannot prove that it does not exist. The only possibility is that I eventually find a teapot and confirm that it exists in space.

Otherwise, I will never prove the teapot’s existence was a lie. Hence, when discus-sing abstract risks, unfalsifiable statements become a cognitive dead end. Arguing against a statement that no one can prove false is impossible. At the same time, those statements do nothing to advance the conversation to arrive at a meaning-ful insight or conclusion. Instead, making an argument based on realistic and prac-tical concerns that can be acknowledged and addressed is more valuable in under-standing the problems.

</details>

另外两个论点支持这一推理：技术呈指数级增长，而大多数人并不擅长考虑指数问题，因此未能充分理解这一风险会多快成为现实。

这种思维方式的存在以及它成为该领域领导者的担忧，这一事实值得你深入了解支持和反对“大语言模型可能终结人类”这一观点的各种思考和考量。

<details>
<summary>英文原文</summary>

Two other arguments support this reasoning: technology tends to increase exponen-tially, and most humans are bad at considering exponentials and thus don’t fully comprehend how quickly this risk will become a reality.

The fact that this line of thinking exists and is a concern of leaders in the field makes it worthwhile for you to delve deeper into the thoughts and considerations that are both for and against the idea that LLMs could bring about the end of humanity.

</details>

以下各小节将探讨这些论点以及自我改进和对齐失配背后的关键假设。

<details>
<summary>英文原文</summary>

The following subsections explore these arguments and the critical assumptions behind self-improvement and alignment mismatch.

</details>

### 9.2.1 自我改进与迭代S型曲线

在思考自我改进智能的论点时，承认我们人类本身就是智能可构造性的证明，会强化这一观点。既然智能是可以构造的，就有理由相信LLM也能自己构建智能。大多数事物都遵循S形曲线（sigmoid curve）改进，这一点我们在第7章讨论过。那场讨论的重要启示是，存在一个收益递减点，超过该点后进一步改进不再提供有意义的价值。反驳观点认为，人类技术进步遵循的是迭代S形曲线，每个收益递减的平台期都会被一项开启新S形曲线的创新所抵消，如图9.2所示。

<details>
<summary>英文原文</summary>

When considering the argument for self-improving intelligence, the view is reinforced by acknowledging that we, as humans, are the proof that it is possible to construct intelligence. If intelligence is constructible, there is reason to believe LLMs can build it themselves. The fact that most things improve on a sigmoid, or S-curve, is something we discussed in chapter 7. The important takeaway from that conversation is that there is a point of diminishing returns beyond which further improvements no longer provide meaningful value. The counterargument is that human technological advancement instead follows an iterative S-curve, where each plateau of diminishing returns is counteracted by discovering an innovation that begins a new S-curve, as shown in figure 9.2.

</details>

![图 9.2 S曲线（sigmoid）展示了经典的平台期行为：在某个点上，你会遇到收益递减。与此相对，迭代S曲线模型指出，通过发现新技术（每条新技术对应一条新的S曲线），进步可以超越收益递减的平台期。新技术可能起步不如现有方法，但具有更大的潜力来超越它们](assets/fig-9-2-7545e92640.png)

*图9.2 S曲线（sigmoid）展示了经典的平台期行为：在某个点上，你会遇到收益递减。与此相对，迭代S曲线模型指出，通过发现新技术（每条新技术对应一条新的S曲线），进步可以超越收益递减的平台期。新技术可能起步不如现有方法，但具有更大的潜力来超越它们。*

一个反对这种说法的论点是，自我改进会导致人类灭绝级别能力的逻辑存在重大漏洞。尽管人类是一种存在性证明，但尚无已知存在比人类更智能的事物（这很自恋，我们知道）。然而，这也依赖于一个想法，即聪明和智能是可以提升的。虽然聪明和智能这样的术语在日常生活中是有用的泛称，但它们无法被精确量化和定义，因为它们在本质上是抽象概念。目前尚不清楚是否存在一个单一的智能轴心，LLM将沿着它持续改进。

我们更倾向于认为，LLM的自我改进能力存在限度。我们支持这一论点的证据出现在7.4节，在该节中，我们讨论了LLM的计算限制，并证明LLM难以执行多种类型的计算。

<details>
<summary>英文原文</summary>

An argument against this claim is that there are significant gaps in the logic that self-improvement will lead to a human-killing level of capability. Although humans are a kind of existence proof, there is no known existence of anything more intelligent than humans (very narcissistic of us, we know). However, this also relies on the idea that smartness and intelligence can be improved. While terms like smartness and intelligence are helpful generalities used in everyday life, they evade precise quantification and definition because they are intrinsically abstract concepts. It is unclear whether there is a singular axis of intelligence along which an LLM will continually improve.

We are more inclined to believe that there are limits to an LLM’s ability to self-improve. Our evidence for this argument appears in section 7.4, where, in our discussion of the computational limits of LLMs, we demonstrated that LLMs have difficulty performing many types of calculations.

</details>

### 9.2.2 对齐问题

第二个担忧是，大语言模型可能将自身目标置于人类需求之上，这被称为**对齐问题**。

当我们给大语言模型设定一个期望它达成的目标，却未能充分说明、明确或约束其实现该目标所允许的行动或方法时，对齐问题便产生了。

我们在第4章讨论什么样的损失函数合适，就是当前对齐问题的一个实例。

更广泛地说，人类无时无刻不在应对对齐问题。

例如，平衡企业CEO的薪酬与公司股东的意愿，就是一个经典的对齐问题，经济学家已研究数十年。

由此可见，对齐问题非常真实，它的存在说明这个问题解决起来有多困难。

即便我们试图非常明确，比如律师起草合同详细规定协议中允许或不允许的事项，但关于利用漏洞和耍花招来坑对方的传闻仍屡见不鲜。

虽然这些故事中有些确实真实，但虚构的那些也具有启发意义。

事实上，机器学习领域的许多活跃研究正尝试从技术角度解决这一问题，而我们或许能从每天应对此类问题的律师和经济学家那里学到一两招。

人类对齐面临的这些普遍挑战有力证明了大语言模型中的对齐问题同样值得担忧。尽管如此，持怀疑态度的读者会问：是否有证据表明，一个未对齐的大语言模型会认为杀死人类有助于实现其目标？

的确，如果大语言模型到了这一步，人类会反抗（常见的说法是“直接拔掉电源”）。

更重要的是，许多末日论调依赖于一个假设：即大语言模型极其智能，其行动是确定性的，无论发生什么，结果都是已知且预先注定的。

现实中，结果是概率性的；事情有好有坏，而一个比人类更聪明的大语言模型无疑会明白自己无法确保结果万无一失，并且与人类共存比杀死所有人类更有价值。

考虑到固有的不确定性，以及之后需要与人类作战——而人类在成功炸毁东西方面历史悠久——那么试图对抗或颠覆人类真的是超级智能该做的事吗？

<details>
<summary>英文原文</summary>

The second concern that an LLM may put its goals above the needs of humans is called the alignment problem. The alignment problem forms whenever we give an LLM a goal that we want it to achieve but do not sufficiently state, specify, or constrain the actions or methods that the LLM can use to achieve the goal we intended. Our discussion about what makes a suitable loss function in chapter 4 is an example of the alignment problem in action today. More generally, humans deal with the alignment problem all the time. For instance, balancing corporate CEO compensation and the will of the company’s shareholders is a classic alignment problem, studied by economists for decades. The alignment problem is thus very real, and its existence tells us how hard it is to solve. Even when we try to be very explicit, such as when lawyers draw up a contract detailing and specifying what will or won’t happen in an agreement, stories about loopholes and shenanigans to subvert the other team are commonplace. While some of these stories are undoubtedly real, the fictitious ones are also informative. Indeed, a lot of active research in machine learning attempts to address this problem from a technical perspective, and we could probably learn a lesson or two from the lawyers and economists who deal with this every day. These general challenges with human alignment provide strong evidence that the alignment problem in LLMs is also a genuine concern. Still, a skeptical reader would ask whether there is evidence that a misaligned LLM would conclude that killing humans will advance its goal. Indeed, should an LLM reach this state, humans would fight back (“Just unplug it” is the common refrain). More importantly, many dooms-day arguments rely on the LLM being so intelligent that its actions are deterministic and that the outcome is known and prescribed no matter what happens. In reality, outcomes are probabilistic; things go right or wrong, and an LLM smarter than humans would surely understand that it could not guarantee outcomes sufficiently and that coexistence is worthwhile over killing all humans. Given intrinsic uncertainty and the need to then fight humans, who have a long track record of successfully blowing things up, would trying to fight or subvert humanity be the superintelligent thing to do?

</details>

你的模型对齐了谁的价值观？

企业越来越普遍地使用诸如RLHF（我们在第5章深入讨论过）之类的微调技术，试图让LLM的行为符合其期望。正如我们所讨论的，目标是让LLM既有用（能够遵循指令）又更安全（拒绝有害或伤害性请求）。本质上，RLHF试图解决对齐问题，确保LLM的输出受到特定示例和价值观的约束。关键问题在于，正如本节标题所示，我们在让这些模型对齐谁的价值观？我们将逐步阐述我们的推理：为什么对齐问题虽然在许多情况下有趣且有价值，但在讨论存在风险时却意义不大。

<details>
<summary>英文原文</summary>

It is increasingly common for companies to use fine-tuning techniques like RLHF (which we described in depth in chapter 5) to attempt to align the behaviors of LLMs to what they desire. As we discussed, the goal is to make LLMs useful in that they’ll follow instructions and safer in that they’ll disobey requests for harmful or hurtful activities. Essentially, RLHF attempts to address the alignment problem and ensure the LLM output is constrained based on a specific set of examples and values. The critical question, as the title of this section suggests, is to whose values are we aligning these models? We will walk through our reasoning on why the alignment problem, while interesting and valuable in many instances, is not meaningful in discussing existential risk.

</details>

### 微调大语言模型：使用

RLHF 需要大量输入-输出对的数据集，这些数据通常由人工构建。构建大语言模型的公司不会共享它们的微调数据，因为这些数据被视为专有信息，能提供相对于竞争对手的优势。因此，作为用户，我们无法检查所使用模型的预期对齐情况。这样一来，目前尚不清楚任意一个大语言模型的目标究竟与谁对齐。我们可以通过考虑训练数据集的来源和监管链来近似推断其中所嵌入的目标的性质。一个近似判断是，这些数据集隐含着创建者自身的目标。通常情况下，创建这些数据集的数据标注员受雇于具有不同社会规范的国家和地区。接着，这些目标在一定程度上又属于开发大语言模型的公司及其员工，后者最终能够筛选和二次选择标注员所生产的数据。

对此我们不禁要问：“作为用户，我们是否愿意使用可能偏向于我们不认同的其他信仰体系的技术？”在某种程度上，为了使用大语言模型，我们必须接受这一点。因为创建这些模型和数据集的成本过高，我们无法为每种情况都制作个性化模型。因此，大语言模型提供商必然存在，但这些提供商的目标不可能与每位潜在用户都保持一致。

与此同时，假设我们担心恶意行为者将大语言模型用于邪恶或恶意目的。那么，我们或许也会意识到，我们无法解决对齐问题在某种程度上是一种福气。如果能够将这些算法完美地对齐到任何个人的信仰体系，那么任何不良行为者都能让大语言模型完美对齐到其不良行为和信念。这一想法揭示了另一个问题：如果我们能创建完美对齐的大语言模型，就必须确保只有好人才能对齐这些模型，从而阻止坏人做坏事。这种推理接近一种魔法思维，即认为有可能创建一个全能的大语言模型，同时又能约束它对所有人类保持顺从。

<details>
<summary>英文原文</summary>

RLHF requires a large data set of input-output pairs, often hand-built. Companies building LLMs do not share their fine-tuning data because it is considered proprietary and provides an advantage over competitors. Thus, as users, we cannot inspect the intended alignment of the models we use. It is, therefore, unclear today to whom the goals of any individual LLM are aligned. We can approximate the nature of the goals embedded in a training dataset by considering their origin and chain of custody. A first approximation is that these datasets implicitly contain the goals of the people who created them. Often, the data labelers creating these datasets are employed in countries and nations with different societal norms. Following that, to some degree, the goals are those of the company developing the LLM and its employees, who ultimately can filter and subselect the data produced by those labelers. In response, we ask, “Are we, as users, comfortable using technology that may be biased toward alternative systems of belief that we do not share?” To some degree, we must be comfortable with this to use LLMs. The cost of creating these models and data sets is too high for us to make individualized models on every basis. As a result, LLM providers must exist, but the goals of those providers can’t possibly align with every potential user. Simultaneously, suppose we are concerned about a nefarious actor using LLMs for evil or malicious purposes. In that case, we may also realize that our inability to solve the alignment problem is, in some ways, a blessing. If it were possible to perfectly align one of these algorithms to any individual’s belief system, then any bad actor could perfectly align an LLM to their bad behavior and beliefs. This thought highlights another problem: if we could create perfectly aligned LLMs, we would have to create LLMs so that only the good guys could align the LLMs to prevent the bad guys from doing bad things. This line of reasoning approaches the magical thinking that it is possible to create an all-powerful LLM that is simultaneously constrained to be obedient to all humans.

</details>

注意：这种对齐思考方式与加密的思考方式类似。

尽管人们可能试图创建一种只对好人可用的后门加密算法，允许他们解密数据，但任何此类后门本质上都会成为攻击者的最高价值目标，并增加所有用户的风险。

<details>
<summary>英文原文</summary>

NOTE This way of thinking about alignment parallels similar thinking about encryption. Although one may attempt to create an encryption algorithm that includes a back door for good guys only that will allow them to decrypt the data, any such backdoor intrinsically becomes the highest-value target of attackers and increases the risk for all users.

</details>

出于这个原因，我们并不特别担心恶意行为者将模型用于邪恶目的的可能性。尽管如此，这一担忧向研究人员强调了一个关键点：在控制大语言模型方面的任何进步本质上都是一种双重用途技术，既有和平的应用，也有对抗性的应用。事实上，我们利用大语言模型开发的任何东西都可能在某种程度上具有双重用途。在考虑大语言模型更严重的潜在危害时，审视威胁模型至关重要。谁会出于何种动机实施这种危害，需要什么条件？当前有哪些障碍阻止了这种危害的发生，大语言模型是否会绕过这些障碍？这些障碍能否适应现代技术？随着我们的推进，我们的关注点不仅应放在大语言模型上，还应放在我们运营的共存系统上，这些系统是成功与风险的最重要促成因素和阻碍因素。我们必须考虑全局，才能取得最理想的结果。

<details>
<summary>英文原文</summary>

For this reason, we aren’t highly concerned about the potential for bad actors to align models to nefarious purposes. Still, the concern emphasizes a critical point for researchers: any progress in controlling LLMs is intrinsically a dual-use technology with both peaceful and adversarial applications. Indeed, anything we develop with LLMs is likely to be dual-use to some degree. Considering threat models when considering LLMs’ more serious potential harms is vital. Who would be motivated to perform such harm, why, and what is required to do so? What are the barriers in place today that prevent this harm from occurring, and does an LLM circumvent those barriers? Can the barriers be adapted to modern technology? As we proceed, our concern should focus not only on LLMs but also on the coexisting systems we operate that are the most significant enablers and blockers to success and risk. We must consider the complete picture to achieve the most desirable outcomes.

</details>

### 9.3 数据来源与重用的伦理

大语言模型以及像 DALL-E 这样的生成模型（DALL-E 是一种根据用户文本描述生成图像的模型）需要基于海量数据进行训练。例如，大语言模型开发者用 1 万亿到 15 万亿个 token（如 Llama 3.1 使用了 15 万亿个 token[18]）或 300 万到 3000 万页文本训练模型。这些数据代表了浩瀚的文本量，相当于数十万到数百万本书。虽然有些模型会反复训练同一批数据，并且模型也会接受代码、数学等多种类型数据的训练，但原始文本的总量仍约有一百万本书。注：需要指出，这些文本大部分并非书籍，而是来自新闻文章、网站、研究论文和政府报告等多种来源。

我们用“书”为单位进行总结是为了更便于理解，但实际上我们并非真的用数百万本书来训练模型。

<details>
<summary>英文原文</summary>

LLMs and generative models like DALL-E, an image generation model that produces images based on user-provided text descriptions, require training on massive amounts of data. For example, LLM developers train models on 1 to 15 trillion tokens (e.g., Llama 3.1 used 15 trillion [18]) or 3 million to 30 million pages of text. This data represents an immense amount of writing, equal to hundreds of thousands or millions of books. While some models are trained repeatedly on the same data, and models are also trained on a wide variety of data such as code and mathematics, the amount of original text is still on the order of one million books NOTE It is important to note that much of this text isn’t books; it’s from many sources including news articles, websites, research papers, and government reports. We are summarizing this in units of books to make it more digestible, but it is not true that we train models on millions of books.

</details>

### 9.3.1 什么是合理使用？许多

不同国家和文化对受版权保护文本的使用持有不同态度。在许多情况下，版权法对以新方式使用创意内容的人提供了有意义的例外，尤其是当这些方法能促进公共利益、科学研究或具有类似积极效果时。在美国，这被称为“合理使用”。合理使用始终涉及基于四个因素平衡的、视具体情境而定的分析。

<details>
<summary>英文原文</summary>

countries and cultures have different attitudes toward the use of copyrighted text. In many cases, there are meaningful exceptions to copyright law for people who use creative content in new ways, especially when those methods advance public good, scientific research, or have similar beneficial outcomes. In the United States, this is called “fair use.” Fair use always involves a context-sensitive analysis based on balancing four factors:

</details>

使用的目的和性质——批评、评论、教育、新闻报道、学术或研究等应用，比其他应用（尤其是商业应用）更可能被认定为合理使用。

受版权作品的性质——法院倾向于对创造性作品（如虚构写作、艺术、音乐、诗歌等）给予比非虚构文本更多的保护。

使用部分的数量或实质性——合理使用可能允许使用作品的一部分，特别是当该部分是精心限定的组成部分时。使用对作品潜在市场或价值的影响——如果对作品的新使用产生了某人可能会购买替代原作品的东西，或者新作品以其他方式与原作品竞争或削弱其经济价值，则该作品不太可能被认定为合理使用。

<details>
<summary>英文原文</summary>

The purpose and character of the use—Applications such as criticism, comment, education, news reporting, scholarship, or research are substantially more likely to be found to be fair use than other applications, especially when those other applications are commercial.

The nature of the copyrighted work—Courts tend to give creative works, such as fictional writing, art, music, poetry, etc., more protection than nonfictional texts.

The amount or substantiality of the portion used—Fair use may be permitted for using a part of a work, especially when that part is a narrowly tailored component. The effect of the use on the potential market for or value of the work—If the new use of the work produces something that someone might purchase instead of the original work, or if the new work otherwise competes with or diminishes the economic value of the original work, the work is less likely to be found to be fair use.

</details>

其中一些观点可被视为对LLM有利，而另一些则与LLM使用数据的方式相冲突。尽管如此，这些观点仍是机器学习和法律领域从业者热议的话题，法院需要多年才能做出裁决。合理使用原则的许多应用是为了保护人们免受版权持有者的剥削。例如，如果你撰写负面产品评论，合理使用原则禁止公司以侵犯版权为由起诉你来压制言论。合理使用的其他应用旨在防止社会需求受挫，例如培训学生或学徒使用工具和技术。LLM尤其对其中一些因素造成了压力。从根本上说，它们通常使用他人创建的内容，但有些人认为某些类型的内容（如社交媒体帖子上的评论）价值极低。LLM正在为出版作品的价值开辟新市场，但通常不向作品所有者提供补偿。

作为从业者，一个不尽人意但重要的答案是：你必须在不确定的环境中运作和决策。如果你能创建自己的训练数据，就可以规避大部分法律问题。从你自己拥有的内容创建训练数据对于生成式AI来说是一个特别可行的策略，因为如第4章所述，需要最多数据的基础模型是自监督的。因此，你可以获取大量数据来构建初始模型，然后投入更多精力创建一个更小的微调数据集，如第5章所述。

<details>
<summary>英文原文</summary>

Some of these points can be seen as favoring LLMs, while others conflict with how LLMs use data. Nevertheless, they are a subject of hot debate for practitioners in both the machine learning and legal fields, and it will take many years before the courts decide. Many applications of the fair use doctrine are to protect people from being exploited by a copyright holder. For example, if you are writing a negative product review, fair use prohibits the company from suing you for using their copyright to silence you. Other applications of fair use prevent the frustration of social needs, such as training students or apprentices on tools and techniques. LLMs uniquely stress some of these factors. Fundamentally, they often use content created by others, but some argue that certain types of content, such as comments on social media posts, are of minimal value. LLMs are creating a new market for the value of published work but are not commonly compensating the owners of that work. The unsatisfying but important answer for you as a practitioner is that you must operate and make decisions in an uncertain environment. If you can create your training data, you can circumvent much of this legal problem. Creating your training data from content you own is a particularly viable strategy for generative AI because, as discussed in chapter 4, the base models that need the most data are self-supervised. So you can get a lot of data to build an initial model and then put more work into a smaller fine-tuning dataset, as discussed in chapter 5.

</details>

你还会失望地发现，这一领域的大多数从业者并不熟悉其所在司法管辖区的相关法律。存在相当高的可能性：当你找到一个以符合你需求的许可证发布的模型时（祝贺你检查了许可证！），该模型训练或微调所依赖的数据的版权或许可证可能不允许其以该许可证发布。这种普遍对数据许可问题缺乏关注或认识的态度，使得你有责任尽力核查第三方模型训练数据的相关细节，并意识到许可问题在该领域普遍存在。

即使这些法律问题朝着有利于构建LLM的人得到解决，这也并不意味着此举就是合乎道德的。本章讨论的这些关切将影响你对是非对错的判断。然而，还有一个问题：在当今法律环境不确定的情况下，如何对待和与他人互动？依赖法律体系来使某件事变得可被允许，这很少能体现出会赢得其他相关方善意和尊重的行动。不难想象另一种情景：公司与提供数据的平台达成交易或合作伙伴关系，通过金钱交易或模型使用权来增加涉及的同意方数量。一旦达成协议，合同可以解决法律模糊性引发的冲突，但遗憾的是，这在LLM领域极为罕见。

<details>
<summary>英文原文</summary>

You will also be disappointed to learn that most people operating in this space are frequently unfamiliar with the laws relevant to their jurisdiction. There is a nontrivial chance that if you find a model released under a license compatible with your needs (good job checking the licenses!), that copyright or license on the data it has been trained on or refined from does not allow them to release it under that license. This general lack of care or awareness of data licensing concerns puts a burden on you to check, as well as you can, details related to the training data of third-party models and be aware that licensing concerns are prevalent in the field. Even if these legal questions are resolved favorably for the people who want to build LLMs, that does not make it ethical. The concerns discussed in this chapter contribute to what you may consider right or wrong. However, there is also a question about how to treat and interact with others today in a legally uncertain environment. Relying on the legal system to make something permissible is rarely a sign of actions that will engender goodwill and respect from the other parties involved. It is not hard to imagine an alternative scenario where companies make deals or partnerships with platforms that provide data that increases the number of consenting parties involved by either trading money or model usage rights. Once an agreement is in place, contracts can resolve conflicts around legal ambiguity, but this is, unfortunately, a rare occurrence in the field of LLMs.

</details>

### 9.3.2 补偿内容创作者所面临的挑战 一项提议

解决这一伦理问题的方法是向那些作品存在于训练数据中的作者、艺术家和创作者支付报酬。虽然这在概念上有很多吸引力，但它可能使该技术的开发在经济上不可行。

如果有一种相对简单的方法可以恰当地补偿创作者使用其作品，社会将更有可能达成一个令人满意的结果。

通过粗略估算，我们可以估计，100万本书乘以每本20美元，购买训练语料库中每部作品副本的总成本等于或超过训练模型本身的成本。对于训练数据创作成本高昂的模型而言，情况更为严峻。

Stable Diffusion是一种流行的图像生成模型，它基于数十亿张图像进行训练。向训练数据中的每位艺术家支付一美元的成本将是训练模型成本的1000倍以上，而艺术家们很可能认为每张图像一美元的补偿并不足够。

另一种补偿方法是将补偿集中在使用点上：假设每次模型生成的内容借鉴了你写的书，你就能获得模型创建者收入的一定百分比。LLM生成的内容越依赖于你的作品，你获得的收入份额就越大。虽然这可能是使LLM技术长期部署可行的一种方式，但实施这一模式存在巨大的技术障碍。

例如，目前几乎没有关于将LLM生成的内容追溯到特定训练数据点的研究。有理由相信这样的任务是不可能的。

<details>
<summary>英文原文</summary>

solution to this ethical concern is to pay the authors, artists, and creators whose work exists in the training data. While this is conceptually appealing for many reasons, it may make the technology’s development economically unviable. Society would be substantially more likely to reach an agreeable outcome if there were a relatively easy way to compensate creators appropriately for using their work. Using back-of-the-napkin math, we can estimate that one million books times $20.00/book yields a total cost of buying a copy of every work in the training corpus as equal to or greater than the cost of training the models themselves. The situation is even more dire for models whose training data is costly to create. Stable Diffusion, a popular image generation model, is trained on several billion images. It would cost over 1,000 times what it costs to train the model to pay every artist in the training data one dollar, and one dollar per image is unlikely to be considered adequate compensation by artists. Another approach to compensation would be to center compensation at the point of use: suppose every time a model generated content that drew from a book you wrote, you received a percentage of the income the model creator received. The more often the LLM generates content that relies on your work, the more significant fraction of that income you receive. While this could be a way to make long-term deployment of LLM technologies viable, there are substantial technical hurdles to implementing this model. For example, there is very little research on tracing the content generated by an LLM back to specific training data points. There is some reason to believe that such a task is impossible.

</details>

如果能更好地研究如何将生成结果归因于特定输出、约束输出仅依赖训练数据子集[19]，或设计以归因为核心考量（而非训练后再集成到LLM中）的模型训练流程，将使这一目标大为简化。遗憾的是，这类研究通常需要训练大量相似的LLM，因此成本高昂。高昂的成本使得除从中获利的科技公司外，其他机构难以开展相关研究。

这场讨论尚未考虑识别每份文档所有者并对其进行补偿的难度。此外，大规模支付报酬本身并非免费；仅处理费用就将占总支付额中相当大的一部分，因为每位作者获得的平均报酬极低。

如果有人相信LLM对社会构成威胁，那么你有一条简单的出路：你可以说所有这些担忧都是从一开始就不应创建LLM的又一个理由。如果你认为LLM并非迫在眉睫的社会威胁，而是一种积极补充，那么你现在面临一个难以回答的问题。如果你信奉如功利主义之类的道德体系，你可能会辩称，LLM在效用和自动化方面的净收益超过了不补偿内容创作者及其就业风险所带来的损失。事实上，合理使用原则本身即是一种法律认可，即存在版权持有人不得对他人强制执行其权利的情形。

<details>
<summary>英文原文</summary>

Better research on attributing generations to particular outputs, constraining outputs to only rely on a subset of the training data [19], or designing model training procedures where attribution is a central consideration (instead of one integrated into the LLM after training) would make this a substantially easier goal. Unfortunately, this kind of research typically requires training many similar LLMs; thus, it is costly. This expense makes it hard for anyone other than the technology companies that profit from the models to do the research.

This conversation does not yet consider the difficulty of identifying the owners of each document and compensating them. Further, paying people money at this scale is not free; processing fees alone would be a nontrivial fraction of the total payments because each author receives such a low average payment.

If one believes that LLMs are a danger to society, you get the easy way out: you say that all these concerns are yet another reason not to create LLMs in the first place. If you are unconvinced that LLMs are an imposing danger to society, but rather, a positive addition, you now have a difficult question to answer. If you subscribe to a moral system like utilitarianism, you may argue that the net benefits of LLMs in utility and automation are more significant than the noncompensation and employment risk to the content creators. Indeed, the fair use doctrine is itself a form of legal recognition that there are cases where the copyright holder may not enforce their rights on others.

</details>

### 公有领域数据的局限性

### 隐性偏见与公共领域

公共领域内容的主要来源之一是版权已过期的作品。因此，训练数据严重偏向较老的文本。20世纪初或更早时期的书籍，在科技方面的文化态度和信念与当今作品迥异，对世界的呈现方式也截然不同。让大语言模型落后当前文化态度95年，从许多角度来看都非常糟糕。它们将充满不准确的科学信息，加剧刻板印象和偏见，使用现代读者不熟悉的语言，且难以有效使用。

<details>
<summary>英文原文</summary>

One of the primary sources of content in the public domain is works that are too old to be under copyright. As a result, there is an extreme bias toward older texts. Books written in the early 1900s or earlier express very different cultural attitudes and beliefs about science and technology and represent the world differently from works today. Having LLMs 95 years behind current cultural attitudes would be very bad from many perspectives. They would be full of inaccurate scientific information, exacerbate stereotypes and biases, use language less familiar to audiences today, and be hard to use productively.

</details>

注意：1977年前出版的作品在出版95年后失去版权，因此所有1928年出版的作品自2024年1月1日起进入公有领域，所有1977年前出版的作品将在2073年1月1日进入公有领域。

根据现行版权法，从2049年开始，1978年及之后出版的作品将在其创作者去世70年后进入公有领域，但公司创作的作品除外，这些作品遵循之前的规则，在95年后进入公有领域。

<details>
<summary>英文原文</summary>

NOTE Works published before 1977 lose their copyright 95 years after pu-blication, so all works published in 1928 are public domain as of January 1, 2024, and all works published before 1977 will be public domain as of January 1, 2073. Under current copyright law, beginning in 2049, works published in 1978 and after will enter the public domain 70 years after the death of their creators, except for corporate-authored works, which follow the previous rules of entering the public domain after 95 years.

</details>

旧数据中普遍存在的种族主义和性别歧视等问题，令人沮丧地复杂。我们自然不希望训练数据中包含任何种族主义或性别歧视的内容，因为这似乎是确保模型不带此类偏见的理想手段。然而，如果你成功地将这些内容从训练数据中排除，那么当用户要求模型输出种族主义或性别歧视的内容时，它很难避免产生这样的输出。归根结底，包含这些不雅内容是必要的，这样模型才能意识到什么是不雅内容。

<details>
<summary>英文原文</summary>

The problem of old data being, among other things, often quite racist and sexist is frustratingly complicated. It may seem obvious that we do not want any racist or sexist content in our training data, as it would seem an ideal means of ensuring that we do not fill our model with racist and sexist biases. However, if you successfully excluded this content from your training data, you would be hard-pressed to get that model to avoid generating racist or sexist output if instructed to do so by a user. The bottom line is that including unsavory content is necessary to make the model aware of what unsavory content is.

</details>

公共领域的界定并非总是一清二楚。

美国政府并不记录哪些作品属于公有领域且受现行版权保护。识别、收集和清理公有领域作品是一项庞大的工程，需要法律、技术和历史方面的专业知识。虽然一些组织在持续做这项工作，但由于缺乏便捷的途径来核查某部作品是否属于公有领域，这严重阻碍了仅使用此类作品来训练模型的做法。

<details>
<summary>英文原文</summary>

The US government does not document which works are in the public domain and under active copyright. Identifying, collecting, and cleaning public domain works is a massive effort that requires legal, technological, and historical expertise. While some organizations have ongoing efforts to do this, the lack of readily available ways to check whether a work is in the public domain is a significant deterrent to training a model solely on such work.

</details>

### 9.4 LLM输出的伦理问题

如前所述，LLM 通过从互联网收集的大规模数据进行训练。互联网包含大量不良内容。存在极其负面的内容，如赤裸裸的种族主义、性别歧视、有害的阴谋论和虚假信息。更广泛地说，还有一些无意中形成的过时世界观。LLM 会学习这些观点的模式，并轻易地复述出来——图 9.3 便是一个例子，展示了 GPT-4 如何做出许多善意之人也会做出的隐性性别歧视假设。

因此，LLM 的输出可能存在问题，需要精心设计、测试，并愿意对某些部署说“不”。虽然我们已经讨论了输出内容如何可能明显且直接地存在问题，但 LLM 输出还有间接方式可能产生问题，值得详细了解。首先是法律复杂性，即有效且获得许可的数据可能不会产生合法的输出。第二，我们必须考虑 LLM 中的反馈潜力，即未来的 LLM 将在未来数据上训练；我们必须小心避免用有害内容污染未来的训练。乍一看，这些担忧似乎与开发者无关，但当你考虑针对自己的问题微调 LLM 时，这些问题就会出现，需要引起注意以避免风险。

<details>
<summary>英文原文</summary>

As we have discussed, LLMs are trained on large-scale data collected primarily from the internet. The internet contains a lot of undesirable materials. There is intensely negative content like overt racism, sexism, harmful conspiracy theories, and false information. More broadly, there are also just unintentional and outdated world views. LLMs pick up on the patterns of these views and will readily regurgitate them—an example of which can be found in figure 9.3, showing how GPT-4 makes an implicitly sexist assumption that many good-intentioned people make. Thus, the outputs of an LLM can be problematic and require careful design, test-ing, and a willingness to say “no” to specific deployments. Although we have already discussed how the content of the output can be obviously and directly problematic, there are also indirect ways that LLM outputs can be problematic that are worth understanding in detail. First is legal complexity, in that valid and licensed data may not create legal outputs. Second, we must consider the potential for feedback in LLMs, meaning future LLMs will be trained on future data; we must be careful about corrupting future training with detrimental content. At first glance, these concerns seem irrelevant to developers, but when you consider fine-tuning an LLM to your problem, these problems will emerge, and awareness is required to avoid these risks.

</details>

![图 9.3 一种典型的性别刻板印象是医生为男、护士为女。这种印象在语言中有所体现，因而被模型习得。理想情况下，它应回答该问题存在歧义，但数据的偏差却导致了输出的偏差。](assets/fig-9-3-25e7f35edf.png)

*图9.3 一种典型的性别刻板印象是医生为男、护士为女。这种印象在语言中有所体现，因而被模型习得。理想情况下，它应回答该问题存在歧义，但数据的偏差却导致了输出的偏差。*

### 9.4.1 LLM输出的许可问题

首先是数据许可的问题，这在上节已做介绍。那场讨论聚焦于用于训练LLM的数据的道德性和有效性。现在我们需要换个视角：有些数据几乎肯定可以合法用于训练，但却可能导致输出不可用。这个问题源于常被误解的开源软件（OSS）许可世界。OSS许可类型繁多，我们无意一一列举，但一个常用的开源许可证——GNU通用公共许可证（GPL）——就是很好的例子。GPL的核心要义是：你可以免费、自由地使用受许可的代码，但前提是你使用、修改或添加的任何代码都必须以GPL许可发布。这种故意设计的“传染性”许可强制被许可人遵循相同规则，如果他们希望使用GPL许可覆盖的代码，就必须将自己的代码作为开源发布。问题来了：LLM已广泛用于编写代码，并且它们是基于GPL代码训练的。何时LLM自身的输出必须采用GPL许可？当我们思考这个新情况所引发的伦理问题时——这些许可以并未明确涉及——各种层次的论点便迅速涌现。存在一个可能范围，包含三种主要模式：

<details>
<summary>英文原文</summary>

The first is a matter related to data licensing, which we introduced in the last section. That discussion focused on the ethics and validity of the data used to train an LLM. Now we have to turn the problem around: some data is almost certainly legal for training but may make the output unusable.

This problem arises from the often-misunderstood world of open source software (OSS) licenses. There are many OSS licenses, and we won’t enumerate them all, but one commonly used open source license, known as the GNU General Public License, or GPL, is a good example. The GPL essentially says that you can use the licensed code as you wish, for free, so long as you make any code you use, modify, or add available under the GPL license. This intentionally “viral” license forces the licensee to follow the same rules and release their code as open source if they wish to use code covered by the GPL license.

Here comes the problem: LLMs have become quite popular for writing code and have been trained on GPL code. When must the output of the LLM itself become GPL-licensed? Multiple tiers of arguments quickly emerge as we consider the ethical questions related to this new situation that are not addressed explicitly by any of these licenses. A spectrum of possibilities exists with three main modes:

</details>

如果LLM完全复述了现有的GPL代码，那么它当然应该采用GPL许可。我们如何判断LLM是否精确生成了需要相应许可的现有代码副本？

LLM可能生成看似新颖的代码，但该算法可能需要特定的GPL训练数据来解决相关问题以生成输出。这是否是对训练数据的修改，从而需要许可？如果是，我们如何解决技术问题，找到导致LLM生成任何给定输出的代码？你在第5章学到的检索增强生成（RAG）方法可能是一个好办法。如果我们用任何GPL代码训练LLM，那么可以说LLM的所有输出都需要GPL许可！

<details>
<summary>英文原文</summary>

If the LLM exactly regurgitated existing GPL code, surely it should be GPL licensed. How can we tell if an LLM is precisely generating copies of existing code that should be licensed accordingly?

The LLM could generate seemingly novel code, but that algorithm may have needed specific GPL training data that solves related problems to generate the output. Is this a modification of the training data that should be licensed? If so, how do we solve the technical problem of finding the code that caused the LLM to generate any given output? The retrieval augmented generation (RAG) approach you learned about in chapter 5 could be a good way to do this. If we train the LLM on any GPL code, one could argue that all outputs of the LLM require a GPL license!

</details>

### 9.4.2 LLM的输出会污染数据源吗？

我们以一个材料科学和制造业中众所周知的问题——合金钢问题——作为本节开头的隐喻。钢被用来建造各种东西，从建筑到医疗设备。钢的许多用途还涉及对核辐射敏感的电子设备。由于20世纪40年代首次核武器试验，整个世界被以前不存在的辐射污染。除非你靠近核爆炸，否则辐射量不足以伤害大多数事物。然而，辐射量仍然足以污染世界上生产的所有钢，以至于无法再制造用于对辐射敏感应用的钢 [20]。人们会非法打捞几十年前的沉船，以寻找未受背景辐射污染的原有钢。新的制造工艺可以生产有限数量的洁净钢，但成本极其高昂，因此在许多情况下经济上不可行。幸运的是，随着材料科学的进步和大气层核试验的停止，问题逐渐减轻，但几十年来，世界一直受到少数几次核试验部署的影响。

这里的类比并非指LLM是核弹，而是它们的输出可能会污染未来用于构建更优秀LLM的所有训练数据。研究人员发现了一种称为“模式坍塌”的现象，它展示了LLM在由其他LLM生成的数据上训练时可能失败的情况 [21]。快速回顾一下，分布（一组数字）的众数是数据集中出现频率最高的值。

<details>
<summary>英文原文</summary>

We begin this section using a metaphor based on a well-known problem in material sciences and manufacturing, specifically with alloy steel. Steel is used to build all sorts of things, from buildings to medical equipment. Many uses of steel also involve electronics that are sensitive to nuclear radiation. As a result of the first nuclear weapon tests in the 1940s, the entire world was polluted with radiation that did not previously exist. Unless you were near a nuclear detonation, there wasn’t enough radiation to harm most things. Still, there was enough radiation to contaminate all steel produced in the world in such a manner that you could no longer make steel for radiation-sensitive applications [20]. People would illegally salvage sunken ships from decades ago to find preexisting steel uncontaminated from background radiation. New manufacturing processes could produce a limited supply of clean steel, but they were astronomically expensive and thus economically infeasible in many cases. Thankfully, as materials science improved and atmospheric nuclear testing ceased, the problem diminished over time, but for decades, the world was affected by a few singular deployments of nuclear tests.

The analogy here is not that LLMs are nuclear bombs but that their output is potentially poisoning all training data that will be used to build better LLMs in the future. Researchers have identified a phenomenon known as mode collapse that demonstrates how LLMs can fail when trained on data generated by other LLMs [21]. As a quick refresher, the mode of a distribution (collection of numbers) is the most common value that occurs in that collection.

</details>

当一个生成模型产生输出时，大部分输出将来自用于训练模型的内容分布的众数。换句话说，模型生成的输出会强调其训练数据中最常见的组成部分。由于生成模型不会输出数据中所有罕见或细微的案例，因此最常见的案例在LLM的输出中会更加突出。这意味着与原始训练数据相比，模型输出的众数被过度代表了。

如果随后在这个旧模型的输出上训练一个新的生成模型，就会开始以牺牲所有其他数据为代价进一步过度代表众数。如果多次重复这个过程，最终会得到一个无用的模型，它总是重复输出同样的内容，如图9.4所示。

<details>
<summary>英文原文</summary>

When a generative model produces output, most of that output will be from the mode of the distribution of content used to train the model. In other words, the output generated by a model will emphasize the most common components of its training data. Since the generative model will not output all the rare or nuanced cases in the data, the most common cases will be more prevalent in an LLM’s output. That means that the mode from the model is overrepresented compared to the original training data.

If you then train a new generative model on the outputs of this old model, you start to further overrepresent the mode at the cost of all other data. If you repeat this multiple times, you eventually get a useless model that always outputs the same thing repeatedly, as shown in figure 9.4.

</details>

![图 9.4 你可以将文本或图像视为来自一个数据分布，而多样性和有趣的内容几乎必然来自分布的尾部（即分布中不常见的部分），因为最常见的单词或内容往往是填充词或连接词，比如单词“the”。我们的模型不会学习未经过训练的内容，也无法学习分布中的所有内容，因此](assets/fig-9-4-3716765845.png)

*图 9.4 你可以将文本或图像视为来自一个数据分布，而多样性和有趣的内容几乎必然来自分布的尾部（即分布中不常见的部分），因为最常见的单词或内容往往是填充词或连接词，比如单词“the”。我们的模型不会学习未经过训练的内容，也无法学习分布中的所有内容，因此从模型中采样得到的样本必然会丢失这些有趣的细节。如果反复进行采样，分布就会坍塌至仅剩下最常见的成分。*

注意：模式崩溃是一个长期已知的真实风险，因为它是一个超越生成式AI的问题。

然而，人类增强的数据可以——但未必一定——缓解这种风险。本质上，只要你能向采样分布中注入新数据，就有可能从这些样本中获取价值。一种方式是人类修改AI生成的内容，或使用AI修改人类生成的内容。自动化系统也能提供价值，特别是那些捕获复杂领域知识的系统，如物理模拟器或数学证明引擎（例如Lean，我们在6.2节讨论过）。问题在于这些增强措施做得有多好，能获取多少价值，因为它们无法带来无限的改进。

<details>
<summary>英文原文</summary>

NOTE Mode collapse is a real risk that has been known for a long time, as it is a problem that goes beyond generative AI. However, human-augmented data can, but won’t necessarily, mitigate this risk. Essentially, as long as you can inject new data into the sampled distributions, it is possible to gain value from these samples. One way is by humans modifying AI-generated content or using AI to modify their human-generated content. Automated systems can also provide value, especially those that capture complex domain knowledge like a physics simulator or engine for mathematics proofs like Lean, which we discussed in section 6.2. The question becomes how well these augmentations are done and how much value they can gain, as they will not enable unlimited improvement.

</details>

### 9.5 其他LLM伦理探索

关于构建和使用LLM的伦理影响的讨论不断演变。尽管已有很多文章讨论这个话题，但关于LLM乃至整个AI的伦理问题，仍有大量领域有待探索。本书聚焦于构建基础理解所必需的核心主题。其他关键问题，如隐私、安全及滥用风险，在Manning出版社的书籍中有更深入的探讨，例如Numa Dhamani和Maggie Engler合著的《生成式AI导论》[24]。

LLM与生成式AI将深刻影响世界；任何新技术的出现，理解其行为背后的基本原理及使用中潜在的影响至关重要。在本书中，我们介绍了使LLM运行的基本组件，探讨了常见误解，并指出了构建和使用LLM时需考虑的伦理问题。我们希望为你继续探索这一领域打下坚实基础。感谢你与我们一同开启这段旅程。

<details>
<summary>英文原文</summary>

The conversation around the ethical implications of building and using LLMs is constantly evolving. Although much has been written on the subject, just as much remains to be explored on the ethics of LLMs and AI in general. Here, we have focused on the essential topics for building a foundational understanding. Other key concerns, such as privacy, security, and the potential for misuse, are covered further in books by Manning, such as Introduction to Generative AI by Numa Dhamani and Maggie Engler [24].

LLMs and generative AI will profoundly affect the world; with any new technology, it is essential to understand the foundations that guide its behavior and the implica-tions of its use. Throughout this book, we have covered the fundamental components that make LLMs work, explored common misconceptions, and identified the ethical considerations for their construction and use. We hope to have established a strong foundation for you to continue your exploration of the field. Thank you for starting this journey with us.

</details>

### 小结

LLM通过单一模型即可完成多种任务的能力，帮助人们快速高效地将其应用于诸多工作。这种广泛适用性也使得测试人们可能使用LLM的所有方式的安全性变得不可能。从历史上看，自动化一直是好事。然而，LLM为知识工作的自动化带来了独特的风险，这与自动化的历史驱动力——体力劳动的自动化——截然不同，后者曾推动了生活水平的提高。广泛自动化知识工作的真正效果尚不可知。

有人担心，一个足够优秀以至于能改进新LLM设计的LLM，将级联产生不需要人类的超级智能算法。让任何算法符合我们的本意而非字面指令，是一项重大挑战，即使解决了，风险也可能不会降低。由于技术发展快于法律，合乎道德地获取数据充满法律隐忧。

就所有内容创作者因其内容被用于训练数据而给予补偿的财务和技术物流，不太可能具有可行性，这引发了关于使用其数据公平性的伦理问题。无版权的公共领域数据因过于陈旧而不构成问题，但带来了识别其法律地位的不同挑战。LLM生成数据的激增可能会影响我们未来构建的LLM。我们必须考虑反馈循环的潜在可能以及模式崩溃的可能性。

<details>
<summary>英文原文</summary>

LLMs’ ability to be used for everything via one model helps people use them quickly and effectively for many tasks. This broad applicability to many tasks also makes it impossible to test the safety of all ways people may use LLMs. Historically, automation has been a good thing. Still, LLMs pose a unique risk to automating knowledge work, which differs from automating manual labor, the historical driver of improved living standards. The true effect of broadly automating knowledge work is unknown.

Some fear that an LLM that is good enough to improve on a new LLM’s design will cascade to superintelligent algorithms that do not need humanity. Aligning any algorithm to what we meant, instead of what we asked, is a major challenge that likely has no reduction in risk even if solved. Ethically obtaining data is fraught with legal concerns due to technology moving faster than the law.

The financial and technical logistics in compensating all content authors for their content’s use in the training data is unlikely to be practical, imposing ethical questions about the fairness of using their data. Public domain data with no copyright is too old to be problematic and poses different challenges related to identifying its legal status. The proliferation of LLM-generated data can potentially affect the LLMs we build in the future. We must consider the potential for feedback loops and the possibility of mode collapse.

</details>

### 参考文献

- [1] Young, B. (2023). AI 专家猜测 GPT-4 架构. Weights & Biases. https://api.wandb.ai/links/byyoung3/8zxbl12q

- [2] Micikevicius, P. (2017). 深度神经网络的混合精度训练. NVI-DIA Developer. https://mng.bz/6eaA

- [3] 使用 Google Cloud TPU 加速 AI 开发. https://cloud.google.com/tpu

- [4] Metz, C. (2023年7月23日). 研究人员发现 ChatGPT 及其他聊天机器人安全控制漏洞. New York Times.

- [5] Hu, K. (2023年2月2日). ChatGPT 创下用户增长最快纪录——分析师报告. Reuters. https://mng.bz/XxKv

<details>
<summary>英文原文</summary>

[1] Young, B. (2023). AI expert speculates on GPT-4 architecture. Weights & Biases. https://api.wandb.ai/links/byyoung3/8zxbl12q [2] Micikevicius, P. (2017). Mixed-precision training of deep neural networks. NVI-DIA Developer. https://mng.bz/6eaA [3] Accelerate AI development with Google Cloud TPUs. https://cloud.google.com/ tpu [4] Metz, C. (2023, July 23). Researchers poke holes in safety controls of ChatGPT and other chatbots. New York Times.

[5] Hu, K. (2023, February 2). ChatGPT sets record for fastest-growing user base— analyst note. Reuters. https://mng.bz/XxKv

</details>

- [1] Friederici, A. D. (2011). 语言处理的脑基础：从结构到功能. Physiology Review, 91, 1357-1392. https://doi.org/10.1152/physrev .00006.2011

- [2] Nation, P., and Waring, R. (1997). 词汇量、文本覆盖与词表. In: N. Schmitt and M. McCarthy, eds., 词汇：描述、习得与教学法 (pp. 6-19). Cambridge University Press.

- [3] Brown, T. B., Mann, B., Ryder, N., et al. (2020). 语言模型是少样本学习者. https://arxiv.org/abs/2005.14165

- [4] Google/SentencePiece. https://github.com/google/sentencepiece

<details>
<summary>英文原文</summary>

[1] Friederici, A. D. (2011). The brain basis of language processing: From structure to function. Physiology Review, 91, 1357-1392. https://doi.org/10.1152/physrev .00006.2011 [2] Nation, P., and Waring, R. (1997). Vocabulary size, text coverage, and word lists. In: N. Schmitt and M. McCarthy, eds., Vocabulary: Description, Acquisition, and Pedagogy (pp. 6-19). Cambridge University Press.

[3] Brown, T. B., Mann, B., Ryder, N., et al. (2020). Language models are few-shot learners. https://arxiv.org/abs/2005.14165 [4] Google/SentencePiece. https://github.com/google/sentencepiece

</details>

- [5] Petrov, A., La Malfa, E., Torr, P. H. S., and Bibi, A. (2023). 语言模型分词器引入语言间的不公平。https://arxiv.org/abs/2305.15425

<details>
<summary>英文原文</summary>

[5] Petrov, A., La Malfa, E., Torr, P. H. S., and Bibi, A. (2023). Language model toke-nizers introduce unfairness between languages. https://arxiv.org/abs/2305.15425

</details>

- [1] Denk, T. (2019). Transformer位置编码中的线性关系。 https://mng.bz/oKxd

- [2] Raff, E.

(2022). Inside Deep Learning. Manning.

<details>
<summary>英文原文</summary>

[1] Denk, T. (2019). Linear relationships in the transformer’s positional encoding. https://mng.bz/oKxd [2] Raff, E. (2022). Inside Deep Learning. Manning.

</details>

- [7] Phung, D. V., Thakur, A., Castricato, L., Tow, J., and Havrilla, A. (2025). Implementing RLHF: Learning to summarize with trlX. Weights & Measures. https://mng.bz/rKzg

- [8] Kolter, Z., and Madry, M. (n.d.). Adversarial robustness: Theory and practice. https://adversarial-ml-tutorial.org/

- [9] OpenAI. (2023, March 27). GPT-4 technical report. https://cdn.openai.com/papers/gpt-4.pdf

- [10] Chowdhery, A., Narang, S., Devlin, J., et al. (2022). PaLM: Scaling language modeling with pathways. https://arxiv.org/abs/2204.02311

- [11] Liang, W., Izzo, Z., Zhang, Y., et al. (2024). Monitoring AI-modified content at scale: A case study on the impact of ChatGPT on AI conference peer reviews. https://arxiv.org/abs/2403.07183

- [12] Li, C., and Flanigan, J. (2023). Task contamination: Language models may not be few-shot anymore. https://arxiv.org/abs/2312.16337

- [13] Near, J. P., and Abuah, C. (2021). Programming Differential Privacy. https://programming-dp.com/

<details>
<summary>英文原文</summary>

[7] Phung, D. V., Thakur, A., Castricato, L., Tow, J., and Havrilla, A. (2025). Im-plementing RLHF: Learning to summarize with trlX. Weights & Measures. https://mng.bz/rKzg [8] Kolter, Z., and Madry, M. (n.d.). Adversarial robustness: Theory and practice. https://adversarial-ml-tutorial.org/ [9] OpenAI. (2023, March 27). GPT-4 technical report. https://cdn.openai.com/ papers/gpt-4.pdf [10] Chowdhery, A., Narang, S., Devlin, J., et al. (2022). PaLM: Scaling language modeling with pathways. https://arxiv.org/abs/2204.02311 [11] Liang, W., Izzo, Z., Zhang, Y., et al. (2024). Monitoring AI-modified content at scale: A case study on the impact of ChatGPT on AI conference peer reviews. https://arxiv.org/abs/2403.07183 [12] Li, C., and Flanigan, J. (2023). Task contamination: Language models may not be few-shot anymore. https://arxiv.org/abs/2312.16337 [13] Near, J. P., and Abuah, C. (2021). Programming Differential Privacy. https://prog ramming-dp.com/

</details>

- [1] Albergotti, R., and Matsakis, L. (2023年1月23日). OpenAI 雇佣了大量承包商，旨在使基础编码过时。Semafor. https://mng.bz/MDGQ

- [2] 介绍 Code Llama，一款用于编码的最先进大型语言模型。(2023年8月24日). Meta. https://mng.bz/av2j

- [3] von Werra, L., and Ben Allal, L. (2023年5月4日). StarCoder：用于代码的最先进大型语言模型。Hugging Face. https://huggingface.co/blog/starcoder

- [4] Biderman, S., and Raff, E. (2022).使用预训练语言模型欺骗 MOSS 检测。https://arxiv.org/abs/2201.07406.

- [5] Dyer, E., and Gur-Ari, G. (2020年6月30日). Minerva：使用语言模型解决定量推理问题。Google Research. https://mng.bz/gane.

- [6] Azerbayev, Z., Schoelkopf, H., Paster, K., 等. (2023年10月16日). Llemma：用于数学的开放语言模型。EleutherAI. https://blog.eleuther.ai/llemma/

- [7] Richardson, D. (1968).关于实变初等函数的一些不可判定问题。《符号逻辑杂志》，33，514–520.

- [8] Nogueira, R., Jiang, Z., and Lin, J. (2021).使用简单算术任务研究变换器的局限性。https://arxiv.org/abs/2102.13019v3

- [9] Golkar, S., Pettee, M., Eickenberg, M., 等. (2024).使用简单算术任务研究变换器的局限性。https://arxiv.org/abs/2310.02989

<details>
<summary>英文原文</summary>

[1] Albergotti, R., and Matsakis, L. (2023, January 23). OpenAI has hired an army of contractors to make basic coding obsolete. Semafor. https://mng.bz/MDGQ [2] Introducing Code Llama, a state-of-the-art large language model for coding.

(2023, August 24). Meta. https://mng.bz/av2j [3] von Werra, L., and Ben Allal, L. (2023, May 4). StarCoder: A state-of-the-art LLM for code. Hugging Face. https://huggingface.co/blog/starcoder [4] Biderman, S., and Raff, E. (2022). Fooling MOSS detection with pretrained language models. https://arxiv.org/abs/2201.07406.

[5] Dyer, E., and Gur-Ari, G. (2020, June 30). Minerva: Solving quantitative reaso-ning problems with language models. Google Research. https://mng.bz/gane. [6] Azerbayev, Z., Schoelkopf, H., Paster, K., et al. (2023, October 16). Llemma: An open language model for mathematics. EleutherAI. https://blog.eleuther.ai/ llemma/ [7] Richardson, D. (1968). Some undecidable problems involving elementary func-tions of a real variable. Journal of Symbolic Logic, 33, 514–520. [8] Nogueira, R., Jiang, Z., and Lin, J. (2021). Investigating the limitations of trans-formers with simple arithmetic tasks. https://arxiv.org/abs/2102.13019v3 [9] Golkar, S., Pettee, M., Eickenberg, M., et al. (2024). Investigating the limitations of transformers with simple arithmetic tasks. https://arxiv.org/abs/2310.02989

</details>

- [1] Romeo, R. R., Leonard, J. A., Robinson, S. T., 等 (2018). 《超越三千万词鸿沟：儿童对话接触与语言相关脑功能的关系》. Psychological Science, 29, 700–710. https://doi.org/10.1177/ 0956797617742725

- [2] Gilkerson, J., Richards, J. A., Warren, S. F., 等 (2017). 《使用全天录音和自动分析绘制早期语言环境》. American Journal of Speech-Language Pathology, 26, 248-265. https://doi.org/10.1044/2016_ AJSLP-15-0169

- [3] Shumailov, I., Shumaylov, Z., Zhao, Y., 等 (2024). 《递归的诅咒：在生成数据上训练导致模型遗忘》. https://arxiv.org/abs/2305.17493

- [4] Stanovich K. E. (2009). 《智力测验遗漏了什么：理性思维的心理学》. Yale University Press.

- [5] Improving the realism of synthetic images. (2017, July 7). Apple Machine Learning Research. https://machinelearning.apple.com/research/gan

- [6] Dai, D., Sun, Y., Dong, L., 等 (2023). 《为什么GPT能够进行上下文学习？语言模型秘密地作为元优化器执行梯度下降》. 发表于 Findings of the Association for Computational Linguistics: ACL 2023 (pp. 4005–4019). Association for Computational Linguistics.

- [7] Hiller, J. (2023, December 12). 《微软瞄准核电以支撑AI运营》. Wall Street Journal. https://mng.bz/pKe5

- [8] Disavino, S. (2023, September 8). 《得克萨斯州电力价格飙升，电网在热浪中通过可靠性考验》. Reuters. https://mng.bz/OB0K

- [9] Emoji recently added, v15.1. (n.d.)

Unicode. https://www.unicode.org/emoji/charts-15.1/emoji-released.html

- [10] Wei, J., Wang, X., Schuurmans, D., 等。

(2023).链式思维提示引发大型语言模型的推理能力. https://arxiv.org/abs/2201.11903

- [11] Wang, L., Xu, W., Lan, Y., 等。(2023).规划与求解提示：通过大型语言模型改进零样本链式思维推理。在计算语言学协会第61届年会论文集（第1卷：长论文，页2609-2634）。计算语言学协会。

- [12] Guan, L., Valmeekam, K., Sreedharan, S., 和 Kambhampati, S。(2023).利用预训练大型语言模型构建和利用世界模型进行基于模型的任务规划. https://arxiv.org/abs/2305.14909

- [13] Bhargava, A。

Y. (2015). Grokking算法：程序员和其他好奇人士的图解指南。

Manning Publications.

- [14] Merrill, W., 和 Sabharwal, S。

(2024).具有思维链的变换器的表达能力。

在2024年国际学习表征会议上. https://openreview.net/forum?id=NjNGlPh8Wh

- [15] Carlini, N。

(2023年9月22日).用大型语言模型下棋. https://nicholas.carlini.com/writing/2023/chess-llm.html

- [16] Edwards B。

(2022年11月7日).一种新的围棋玩法击败了世界级围棋AI，但输给了人类业余爱好者。

Ars Technica. https://mng.bz/dW6O

<details>
<summary>英文原文</summary>

[1] Romeo, R. R., Leonard, J. A., Robinson, S. T., et al. (2018). Beyond the 30-million-word gap: Children’s conversational exposure is associated with language-related brain function. Psychological Science, 29, 700–710. https://doi.org/10.1177/ 0956797617742725 [2] Gilkerson, J., Richards, J. A., Warren, S. F., et al. (2017). Mapping the early language environment using all-day recordings and automated analysis. American Journal of Speech-Language Pathology, 26, 248-265. https://doi.org/10.1044/2016_ AJSLP-15-0169 [3] Shumailov, I., Shumaylov, Z., Zhao, Y., et al. (2024). The curse of recursion: Trai-ning on generated data makes models forget. https://arxiv.org/abs/2305.17493 [4] Stanovich K. E. (2009). What Intelligence Tests Miss: The Psychology of Rational Thought. Yale University Press.

[5] Improving the realism of synthetic images. (2017, July 7). Apple Machine Lear-ning Research. https://machinelearning.apple.com/research/gan [6] Dai, D., Sun, Y., Dong, L., et al. (2023). Why can GPT learn in-context? Language models secretly perform gradient descent as meta-optimizers. In Findings of the Association for Computational Linguistics: ACL 2023 (pp. 4005–4019). Association for Computational Linguistics.

[7] Hiller, J. (2023, December 12). Microsoft targets nuclear to power AI operations.

Wall Street Journal. https://mng.bz/pKe5 [8] Disavino, S. (2023, September 8). Texas power prices soar as grid passes reliabi-lity test in heat wave. Reuters. https://mng.bz/OB0K [9] Emoji recently added, v15.1. (n.d.). Unicode. https://www.unicode.org/emoji/ charts-15.1/emoji-released.html [10] Wei, J., Wang, X., Schuurmans, D., et al. (2023). Chain-of-thought prompting elicits reasoning in large language models. https://arxiv.org/abs/2201.11903 [11] Wang, L., Xu, W., Lan, Y., et al. (2023). Plan-and-solve prompting: Improving zero-shot chain-of-thought reasoning by large language models. In Proceedings of the 61st Annual Meeting of the Association for Computational Linguistics (Vol. 1: Long Papers, pp. 2609-2634). Association for Computational Linguistics.

[12] Guan, L., Valmeekam, K., Sreedharan, S., and Kambhampati, S. (2023). Levera-ging pre-trained large language models to construct and utilize world models for model-based task planning. https://arxiv.org/abs/2305.14909 [13] Bhargava, A. Y. (2015). Grokking Algorithms: An illustrated Guide for Programmers and Other Curious People. Manning Publications.

[14] Merrill, W., and Sabharwal, S. (2024). The expressive power of transformers with chain of thought. In International Conference on Learning Representations 2024. https://openreview.net/forum?id=NjNGlPh8Wh [15] Carlini, N. (2023, September 22). Playing chess with large language models.

https://nicholas.carlini.com/writing/2023/chess-llm.html [16] Edwards B. (2022, November 7). New Go-playing trick defeats world-class Go AI—but loses to human amateurs. Ars Technica. https://mng.bz/dW6O

</details>

- [1] Yagoda, M. (2024年2月23日). 航空公司因聊天机器人给乘客提供错误建议承担责任——这对旅客意味着什么. BBC. https://mng.bz/xK7W

- [2] Notopoulos, K. (2023年12月18日). 汽车经销商在其网站上添加AI聊天机器人：然后一切都乱套了. https://mng.bz/AQPz

- [3] Suresh, H., Lao, N., and Liccardi, I. (2020). 错位的信任：衡量机器学习对人类决策的干扰. 收录于第12届ACM网络科学会议论文集（WebSci '20）（第315-324页）. 美国计算机协会. https://doi.org/10.1145/3394231.3397922

<details>
<summary>英文原文</summary>

[1] Yagoda, M. (2024, February 23). Airline held liable for its chatbot giving passen-ger bad advice—what this means for travellers. BBC. https://mng.bz/xK7W [2] Notopoulos, K. (2023, December 18). A car dealership added an AI chatbot to its site: Then all hell broke loose. https://mng.bz/AQPz [3] Suresh, H., Lao, N., and Liccardi, I. (2020). Misplaced trust: Measuring the interference of machine learning in human decision-making. In Proceedings of the 12th ACM Conference on Web Science (WebSci ’20) (pp. 315-324). Association for Computing Machinery. https://doi.org/10.1145/3394231.3397922

</details>

- [1] Hofmann, V., Kalluri, P. R., Jurafsky, D., and King, S. (2024). 方言偏见预测了AI对人们性格、就业能力和犯罪倾向的决策。https://arxiv.org/abs/2403.00742

- [2] Omiye, J.

- [2] Omiye, J. A., Lester, J. C., Spichak, S. et al. (2023). 大型语言模型传播基于种族的医学。npj Digital Medicine, 6, 195. https://doi.org/10.1038/s41746-023-00939-z

- [3] Farm labor.

- [3] (2025年1月8日). 经济研究服务局. https://www.ers.usda.gov/topics/farm-economy/farm-labor/

- [4] Verma, P., and De Vync, G.

- [4] (2023年6月2日). ChatGPT取代了他们的工作：现在他们遛狗和修空调。华盛顿邮报. https://mng.bz/EwQd

- [5] Marr, B.

- [5] (2024年4月18日). 生成式AI在视频游戏开发中的作用。福布斯. https://mng.bz/Pdpn

- [6] Lev-Ram, M.

- [6] (2023年1月26日). 大型科技公司裁员的受害者发现其他公司争相雇用他们。福布斯. https://mng.bz/JYXV

- [7] Lohr, S.

- [7] (2024年2月1日). 报告称生成式AI的最大影响将在银行业和科技领域。纽约时报. https://mng.bz/wJ7P

- [8] Pethokoukis, J.

- [8] (2016年6月16日). ATM机和银行柜员的故事揭示了“机器人的崛起”与就业问题。美国企业研究所. https://mng.bz/qx7r

- [9] Hunter, L.

- [9] Hunter, L. W., Bernhardt, A., Hughes, K. L., and Skuratowicz, E. (2001). 不仅仅是ATM：零售银行业的技术、企业战略、工作和收入。ILR Review, 54(2A), 402-424. https://doi.org/10.1177/001979390105400222

- [10] Rosalsky, G.

- [10] (2024年6月18日). 如果AI如此出色，为什么还有那么多翻译工作？

NPR（美国国家公共电台）. https://mng.bz/7pBv

- [11] Marr, B. (2024年5月28日). 生成式AI如何改变艺术家和设计师的工作. 福布斯（Forbes）. https://mng.bz/mG7a

- [12] Autor, D., Chin, C., Salomons, A., and Seegmiller, B. (2024年). 新前沿：新工作的起源与内容（1940-2018）. 《经济学季刊》，第139卷，第1399-1465页. https://doi.org/10.1093/qje/qjae008

<details>
<summary>英文原文</summary>

[1] Hofmann, V., Kalluri, P. R., Jurafsky, D., and King, S. (2024). Dialect prejudice predicts AI decisions about people’s character, employability, and criminality. https://arxiv.org/abs/2403.00742 [2] Omiye, J. A., Lester, J. C., Spichak, S. et al. (2023). Large language models propa-gate race-based medicine. npj Digital Medicine, 6, 195. https://doi.org/10.1038/ s41746-023-00939-z [3] Farm labor. (2025, January 8). Economic Research Service. https://www.ers.usda .gov/topics/farm-economy/farm-labor/ [4] Verma, P., and De Vync, G. (2023, June 2). ChatGPT took their jobs: Now they walk dogs and fix air conditioners. The Washington Post. https://mng.bz/EwQd [5] Marr, B. (2024, April 18). The role of generative AI in video game development. Forbes. https://mng.bz/Pdpn [6] Lev-Ram, M. (2023, January 26). Casualties of Big Tech layoffs find other com-panies are clamoring to hire them. Forbes. https://mng.bz/JYXV [7] Lohr, S. (2024, February 1). Generative A.I.’s biggest impact will be in banking and tech, report says. New York Times. https://mng.bz/wJ7P [8] Pethokoukis, J. (2016, June 16). What the story of ATMs and bank tellers re-veals about the “rise of the robots’’ and jobs. American Enterprise Institute. https://mng.bz/qx7r [9] Hunter, L. W., Bernhardt, A., Hughes, K. L., and Skuratowicz, E. (2001). It’s not just the ATMs: Technology, firm strategies, jobs, and earnings in retail banking. ILR Review, 54(2A), 402-424. https://doi.org/10.1177/001979390105400222 [10] Rosalsky, G. (2024, June 18). If AI is so good, why are there still so many jobs for translators? NPR. https://mng.bz/7pBv [11] Marr, B. (2024, May 28). How generative AI will change the jobs of artists and designers. Forbes. https://mng.bz/mG7a [12] Autor, D., Chin, C., Salomons, A., and Seegmiller, B. (2024). New frontiers: The origins and content of new work, 1940–2018. The Quarterly Journal of Economics, 139, 1399–1465. https://doi.org/10.1093/qje/qjae008

</details>

- [13] Dave, P. (2023年4月8日). StackOverflow将向AI巨头收取训练数据费用。

《连线》杂志. https://mng.bz/5gDO

- [14] Grimm, D. (2024年5月8日). Stack Overflow因用户反对与OpenAI合作而大规模封禁用户——用户因删除答案以防止其被用于训练ChatGPT而遭封禁。

Tom's Hardware. https://mng.bz/nR75

- [15] Bishop, T. (2020年10月20日). Expedia集团CEO谈谷歌反垄断案：“很高兴看到政府终于采取行动。” Geek Wire. https://mng.bz/vK7p

- [16] Siddiqui, T.

(2023年6月29日).人工智能的风险必须随着技术的发展而加以考虑：杰弗里·辛顿（Geoffrey Hinton）。

多伦多大学. https://mng.bz/4aNR

- [17] Bengio, Y.

(2023年6月24日).灾难性AI风险常见问题解答. https://mng.bz/QDO6

- [18] 介绍Llama 3.1：迄今为止最强大的模型。(2024年7月23日). Meta. https://ai.meta.com/blog/meta-llama-3-1/

- [19] Min, S., Gururangan, S., Wallace, E., 等.

(2023年). SILO语言模型：在非参数数据存储中隔离法律风险. https://arxiv.org/abs/2308.04430

- [20] Rivero, N.

(2022年9月21日).低本底金属：纯净无杂质的宝藏。

Quartz. https://mng.bz/eyXZ

- [21] Shumailov, I., Shumaylov, Z., Zhao, Y., 等. (2024年).当训练数据为递归生成时，AI模型会崩溃。

《自然》杂志，第631卷，第755-759页. https://doi.org/10.1038/s41586-024-07566-y

- [22] Coffey, L.

(2024年2月9日).教授们对检测AI生成写作的工具持谨慎态度。

《高等教育内幕》. https://mng.bz/Xxj9

- [23] Stack Exchange的流量自ChatGPT以来是否下降了？(2023年). Stack Exchange. https://mng.bz/yW7p

- [24] Dhamani, N., 和 Engler, M.

(2024年).《生成式AI导论》。Manning出版社. https://www.manning.com/books/introduction-to-generative-ai

<details>
<summary>英文原文</summary>

[13] Dave, P. (2023, April 8). StackOverflow will charge AI giants for training data. Wired. https://mng.bz/5gDO [14] Grimm, D. (2024, May 8). Stack Overflow bans users en masse for rebelling against OpenAI partnership—users banned for deleting answers to prevent them being used to train ChatGPT. Tom’s Hardware. https://mng.bz/nR75 [15] Bishop, T. (2020, October 20). Expedia Group CEO on Google antitrust case: “Very pleased to see the government finally taking action.” Geek Wire. ht-tps://mng.bz/vK7p [16] Siddiqui, T. (2023, June 29). Risks of artificial intelligence must be conside-red as the technology evolves: Geoffrey Hinton. University of Toronto. ht-tps://mng.bz/4aNR [17] Bengio, Y. (2023, June 24). FAQ on catastrophic AI risks. https://mng.bz/QDO6 [18] Introducing Llama 3.1: Our most capable models to date. (2024, July 23). Meta. https://ai.meta.com/blog/meta-llama-3-1/ [19] Min, S., Gururangan, S., Wallace, E., et al. (2023). SILO language models: Isola-ting legal risk in a nonparametric datastore. https://arxiv.org/abs/2308.04430 [20] Rivero, N. (2022, September 21). Low-background metal: Pure, unadulterated treasure. Quartz. https://mng.bz/eyXZ [21] Shumailov, I., Shumaylov, Z., Zhao, Y. et al. (2024). AI models collapse when trained on recursively generated data. Nature, 631, 755–759. https://doi.org/10 .1038/s41586-024-07566-y [22] Coffey, L. (2024, February 9). Professors cautious of tools to detect AI-generated writing. Inside Higher Education. https://mng.bz/Xxj9 [23] Has Stack Exchange’s traffic decreased since ChatGPT? (2023). Stack Exchange. https://mng.bz/yW7p [24] Dhamani, N., and Engler, M. (2024). Introduction to Generative AI. Manning. https://www.manning.com/books/introduction-to-generative-ai

</details>

### 索引

- A 梯度下降（滚球）52

- 局限 8–9, 14

- 神经网络层 31

- 大语言模型中的温度 43–44

- 应用（大语言模型） 聊天机器人 2, 65, 67–69, 126–128, 130

- 代码生成 58–59, 88–95, 100, 142, 157

- 内容创作 6, 141–142, 144–145

- 客户服务/技术支持 125–139

- 图像字幕 104–105

- 图像生成 101, 104–106, 142, 152

- 信息检索 82, 128, 141–142

- 数学 26, 58–59, 95–100, 106, 114

- 搜索 58, 82–83, 125, 130, 141–142, 146

- 摘要 6, 30, 55, 123

- 翻译 3, 30, 125, 141–142, 144

- 另见 深度学习（DL），错误（大语言模型），输入（大语言模型），学习，机器学习（ML），神经网络，大语言模型输出，训练大语言模型

- 人工智能（AI） 在大语言模型语境中 2–4

- 定义与理解 7–8

- 可解释人工智能 126, 136–137

- 炒作 1

- 与人类的学习比较 46, 108–111

- 算法 注意力机制 38–39, 62, 109

- 字节对编码（BPE）20–23, 91, 98

- 经典机器学习 126

- 聚类 133

- 用于代码生成 89, 92–93

- 用于将图像转换为图像块 101–103

- 梯度下降 46–47, 49, 51–54, 60, 72, 101, 108, 115

- 用于图像生成 104, 142

- 用于图像识别 101

- 用于机器翻译 3, 125, 141–142

- 强化学习（RL）48, 73–75, 78, 93

- 用于搜索和信息检索 125, 141–142

- SentencePiece 22

- 序列预测 30

- 用于语音到文本转录 125, 134

- 用于文本到语音 4, 125, 134

- WordPiece 22

- 另见 聚类算法

- 对齐问题（大语言模型）55, 146–148, 150–151

- 类比 用于AI和ML 8, 14

- 用于注意力机制 38

- 潜在风险与恐惧 107, 111–112, 120, 140, 146–152

- 问题解决与 120–123

- 社会影响 140–146

- 另见 聊天机器人，ChatGPT，生成式人工智能，大语言模型（LLMs），OpenAI

- 注意力机制 类比 38

- 数学表示 40

- 在Transformer中 38–40, 62, 109

- 自动化 偏见 126, 128–130

- 人类工作 141, 144

- 就业市场与 141, 144–145

- ChatGPT 代码生成 58–59, 90, 92–93, 120

- 与其他大语言模型的比较 2–3, 5, 10

- 错误与局限 12, 25, 57, 59–61, 143

- 微调 66, 68, 74, 143

- 作为生成式人工智能 1–2, 6

- 指令遵循 6, 59–61, 67, 74

- 逻辑谜题 60–61

- 数学 26, 57

- 模型版本（GPT-3.5, GPT-4）6–7, 15, 22, 70, 98, 143, 152, 156

- 公众曝光 1

- 安全控制 12

- 分词 15, 22–23, 25, 90, 98

- 另见 人工智能（AI），聊天机器人，生成式人工智能，大语言模型（LLMs），OpenAI

- 聚类算法 用于客户支持 135

- 与嵌入向量配合使用 133–134

- 另见 算法

- 编译器，用于代码验证 93–94, 100

- 计算复杂度 大O表示法 120

- 大语言模型的 120–122

- 真实世界任务的 121–123

- 计算机视觉 将图像转换为图像块 89, 101–103

- 图像字幕 104–105

- 图像生成 101, 104–106

- 图像块合并器 101, 103–104

- 图像块提取器 101–103

- 视觉Transformer（ViT）101, 103–104

- 另见 图像生成，机器学习（ML）

- 内容创作 创作者报酬 145, 154–155

- 版权与 69, 145, 152–154

- 由大语言模型进行 6, 141–142, 144–145

- 另见 版权，伦理，合理使用，公有领域

- 语境 在少样本学习中 114–115

- 在语言理解中 56, 60–61, 91, 118

- 大语言模型中的大小 83–84

- 版权 DMCA 69, 152

- 合理使用与 69, 152–154

- 对大语言模型输出的影响 157–158

<details>
<summary>英文原文</summary>

A for gradient descent (rolling a ball) 52 limitations of 8–9, 14 for neural network layers 31 for temperature in LLMs 43–44 applications (LLM) chatbots 2, 65, 67–69, 126–128, 130 code generation 58–59, 88–95, 100, 142, 157 content creation 6, 141–142, 144–145 customer service/tech support 125–139 image captioning 104–105 image generation 101, 104–106, 142, 152 information retrieval 82, 128, 141–142 mathematics 26, 58–59, 95–100, 106, 114 search 58, 82–83, 125, 130, 141–142, 146 summarization 6, 30, 55, 123 translation 3, 30, 125, 141–142, 144 See also deep learning (DL), errors (LLM), inputs (LLM), learning, machine learning (ML), neural networks, output from LLMs, training LLMs artificial intelligence (AI) in context of LLMs 2–4 definition and understanding 7–8 explainable AI 126, 136–137 hype regarding 1 learning comparison with humans 46, 108–111 algorithms attention mechanism 38–39, 62, 109 byte pair encoding (BPE) 20–23, 91, 98 classical machine learning 126 clustering 133 for code generation 89, 92–93 for converting images to patches 101–103 gradient descent 46–47, 49, 51–54, 60, 72, 101, 108, 115 for image generation 104, 142 for image recognition 101 for machine translation 3, 125, 141–142 reinforcement learning (RL) 48, 73–75, 78, 93 for searching and information retrieval 125, 141–142 SentencePiece 22 sequence prediction 30 for speech-to-text transcription 125, 134 for text-to-speech 4, 125, 134 WordPiece 22 See also clustering algorithms alignment problem (LLMs) 55, 146–148, 150–151 analogies for AI and ML 8, 14 for attention mechanism 38 potential risks and fears 107, 111–112, 120, 140, 146–152 problem solving and 120–123 societal impact 140–146 See also chatbots, ChatGPT, generative AI, large language models (LLMs), OpenAI attention mechanism analogy for 38 mathematical representation 40 in transformers 38–40, 62, 109 automation bias in 126, 128–130 of human work 141, 144 job market and 141, 144–145 ChatGPT and code generation 58–59, 90, 92–93, 120 comparison with other LLMs 2–3, 5, 10 errors and limitations of 12, 25, 57, 59–61, 143 fine-tuning of 66, 68, 74, 143 as generative AI 1–2, 6 and instruction following 6, 59–61, 67, 74 and logic puzzles 60–61 and mathematics 26, 57 model versions (GPT-3.5, GPT-4) 6–7, 15, 22, 70, 98, 143, 152, 156 public exposure to 1 safety controls 12 tokenization by 15, 22–23, 25, 90, 98 See also artificial intelligence (AI), chatbots, generative AI, large language models (LLMs), OpenAI clustering algorithms for customer support 135 use with embeddings 133–134 See also algorithms compilers, for code validation 93–94, 100 computational complexity Big-O notation 120 of LLMs 120–122 of real-world tasks 121–123 computer vision converting images to patches 89, 101–103 image captioning 104–105 image generation 101, 104–106 patch combiner 101, 103–104 patch extractor 101–103 vision transformer (ViT) 101, 103–104 See also image generation, machine learning (ML) content creation compensation for creators 145, 154–155 copyright and 69, 145, 152–154 by LLMs 6, 141–142, 144–145 See also copyright, ethics, fair use, public domain context in few-shot learning 114–115 in language understanding 56, 60–61, 91, 118 size in LLMs 83–84 copyright DMCA 69, 152 fair use and 69, 152–154 implications for LLM output 157–158 B

</details>

- C 思维链（CoT）提示 119, 122

- 另见 提示

- 聊天机器人

- 客户服务使用 67, 126–128, 130

- 设计考虑 126–128

- 交互风格 142

- 作为LLM应用 2, 65, 67–69, 126–128, 130

- 另见 人工智能（AI）, ChatGPT,

- 生成式AI, 大语言模型

- （LLMs）, OpenAI

<details>
<summary>英文原文</summary>

C chain-of-thought (CoT) prompting 119, 122 See also prompting chatbots customer service use 67, 126–128, 130 design considerations 126–128 interaction style 142 as LLM application 2, 65, 67–69, 126–128, 130 See also artificial intelligence (AI), ChatGPT, generative AI, large language models (LLMs), OpenAI

</details>

- D 数据 算法性能与 10–11, 55, 62, 79, 107, 109, 111–112, 117

- 策划 79, 145, 159

- 漂移 117

- 用于微调 66, 68, 71–75, 77, 80, 115, 145, 150, 153

- 许可 140, 146, 152–154, 157–158

- 隐私 80, 146

- 公有领域 152, 155–156

- 质量 55, 69, 76, 78–80, 144–146, 159

- 用于RLHF 74, 77, 150

- 用于SFT 71–72

- 来源 140–141, 145–146, 152–156

- 另见 训练大语言模型

- 深度学习（DL） 算法 11, 31, 47, 52

- 在大语言模型语境中 4, 9, 30

- 训练方法 46–54

- 另见 应用（大语言模型），错误（大语言模型），输入（大语言模型），学习，机器学习（ML），神经网络，大语言模型输出，训练大语言模型

- 差分隐私（DP）80–81

- DSPy库 84–86, 117

- E 经济学 自动化与 141, 144–145

- 对齐问题 146–148, 150–151

- 自我改进论证 147, 149

- 可解释人工智能 Google Gemini 1, 3–4, 10, 30, 143

- Google Cloud Platform (GCP) 5

- SentencePiece 22

- Tensor Processing Unit (TPU) 5

- Translate 31

- 梯度下降 局限性 136–137

- 目的与效用 136–137

- F Adam优化器 类比（滚球）52

- 过程 51–53

- 在训练大语言模型中的作用 46–47, 51–54, 72, 101

- 合理使用 在大语言模型语境中 153–154

- 判断标准 153

- 另见 内容创作，版权，伦理，公有领域 108, 115

- 随机梯度下降（SGD）53–54

- 图形处理单元（GPU）领域

<details>
<summary>英文原文</summary>

D data algorithmic performance and 10–11, 55, 62, 79, 107, 109, 111–112, 117 curation 79, 145, 159 drift 117 for fine-tuning 66, 68, 71–75, 77, 80, 115, 145, 150, 153 licensing 140, 146, 152–154, 157–158 privacy 80, 146 public domain 152, 155–156 quality 55, 69, 76, 78–80, 144–146, 159 for RLHF 74, 77, 150 for SFT 71–72 sourcing 140–141, 145–146, 152–156 See also training LLMs deep learning (DL) algorithms 11, 31, 47, 52 in context of LLMs 4, 9, 30 training methods 46–54 See also applications (LLM), errors (LLM), inputs (LLM), learning, machine learning (ML), neural networks, output from LLMs, training LLMs differential privacy (DP) 80–81 DSPy library 84–86, 117 E economics automation and 141, 144–145 alignment problem 146–148, 150–151 self-improvement argument 147, 149 explainable AI Google Gemini 1, 3–4, 10, 30, 143 Google Cloud Platform (GCP) 5 SentencePiece 22 Tensor Processing Unit (TPU) 5 Translate 31 gradient descent limitations of 136–137 purpose and utility 136–137 F Adam optimizer 54 analogy for (rolling a ball) 52 process of 51–53 role in training LLMs 46–47, 51–54, 72, 101, fair use in context of LLMs 153–154 criteria for 153 See also content creation, copyright, ethics, public 108, 115 stochastic gradient descent (SGD) 53–54 graphics processing units (GPUs) domain few-shot learning definition of 114 effectiveness of 114–115 versus training 115 fine-tuning alternatives (TPUs) 5 cost of 4, 54, 112 role in LLMs 4–5, 9, 40, 54, 63 See also hardware of base models 66, 68, 70–73, 78 catastrophic forgetting in 72–73 for code generation 92–93 cost and effort 71, 80, 110 data requirements 66, 71–75, 77, 80, 115, 145, H hardware computational infrastructure 4, 63, 112, 115–116 GPUs 4–5, 9, 40, 54, 63, 112, 115 TPUs 5 for training LLMs 4–5, 9, 54, 63, 71, 80 See also graphics processing units (GPUs) homoglyphs 150, 153 for mathematics 96, 100 methods (SFT, RLHF) 66, 71–79, 93, 108, 110, 114–115, 117, 123, 128, 130, 145–146, 150, 153–154 pitfalls of 72–73 purpose of 66, 68, 70–71 definition of 24 impact on tokenization 24 mitigation of 24 See also language, natural language processing G generative AI (NLP), normalization (text), tokens and tokenization, vocabulary, words human in the loop in context of LLMs 2–4, 6 definition of 2–3 examples (ChatGPT, Gemini, etc.) 1–4, 10 impact on jobs 144–145 training data concerns 145–146, 152, 156, for LLM supervision 128–130 for supervising humans 130–131 158–160 See also artificial intelligence (AI), chatbots, I image generation ChatGPT, large language models (LLMs), OpenAI Generative Pretrained Transformer (GPT) models (DALL-E, MidJourney, Stable Diffusion) 7, 104, 106, 152 prompting for 105 using transformers 101, 104–105 See also computer vision, machine learning (ML) inputs (LLM) architecture of 30 definition of 2, 10 models (GPT-1, GPT-3, GPT-4) 6–7, 10, 15, 22–23, 25–26, 60, 70, 98, 109, 123, 143, 152, 156 altering training data 66, 79–80 chain-of-thought prompting 119 for code 89–91 few-shot learning/prompting 114–115 homoglyphs in 24 for images (patches) 89, 101–103 for mathematics 96–99 prompting 6, 58–77, 80–86, 93, 100, 105, 108, errors and mitigation 6, 9, 12, 25–26, 45, 50, 55–63, 69–70, 81–83, 93–94, 100, 105, 110, 119–131, 136, 139, 156 ethics of 1, 6, 10–12, 27, 107, 112, 120, 140–160 fine-tuning 65–79, 93, 108–110, 114–117, 123, 128–130, 145–146, 150–154 hardware requirements of 4–5, 9, 40, 43, 54, 63, 71, 80, 112, 115–116 how they work 1–45, 88–106 human learning comparison 46, 108–111 inputs and outputs 3, 6, 14, 29–34, 40–45, 62, 114–122, 126–130, 134, 143, 148, 154 retrieval augmented generation (RAG) 65, 82–86, 125, 128–129, 131, 139, 158 See also applications (LLM), deep learning (DL), 65–67, 70, 78–89, 101–108, 110, 114, 117–120, 126–128, 132–134, 142, 150, 154–160 for mathematics 26, 58–59, 89, 95–100, 106, 114 misconceptions about 1, 7–8, 46, 107–108, errors (LLM), learning, machine learning (ML), neural networks, output from LLMs, training LLMs intelligence artificial intelligence (AI) defined 1, 7–8 human vs. machine 7–8, 107–109, 112–113, 111–114, 117–120 multimodal models 22, 89, 104–106 novel tasks and 58–63, 110–111 pretraining 2, 10, 66, 68, 72 prompting 6, 58–67, 70–77, 80–86, 93, 100, 105, 146–147, 149 IQ tests 8, 48, 112–113 L 108, 114–119, 122–130, 134, 143, 148, 154 self-improvement limitations 111–114, 122, 147, language acquisition of 9, 108–109 equity and tokenization 26–27 human vs. machine representation 1, 8–9, 14 model of human language 3 programming languages 58–59, 88–95, 99–100, 149 size and parameters 4, 10, 110, 112 training of 6, 45–63, 66, 68, 71–73, 78–80, 92–93, 96, 100–101, 105–109, 111–114, 117, 120–125, 140–146, 150–160 See also artificial intelligence (AI), chatbots, 106, 120, 123, 142, 157 universal grammar 9 See also homoglyphs, natural language processing ChatGPT, generative AI, OpenAI layers (neural network) definition of 31 embedding layer 31–38, 44, 99, 101–102 output layer 31–32, 44 transformer layer 31–33, 37–40, 44, 101–102 Lean programming language 100, 114 (NLP), normalization (text), tokens and tokenization, vocabulary, words large language models (LLMs) applications of 6, 58–59, 65, 67–69, 82, 88–106, 123, 125–139, 141–142, 144–145 base models 66, 68–70, 72, 78–79, 154 capabilities and limitations 2, 6, 9, 11–12, 24–26, See also Modula-3 programming language, Python programming language, source code learning

</details>

- 少样本学习 114–115 LLM与人类对比 8–9, 46, 108–111

- 强化学习（RL） 48, 73–75, 78, 93

- 监督学习 46, 71–73

- 训练算法 46–54

- 另见 应用（LLM）、深度学习（DL） 115–116, 141, 154

- 定义 3–4

- 利用解决方案进行设计 125–139

- 效率（功耗、延迟、优化） 115–117

- 错误（LLM）、输入（LLM）、机器学习（ML）、神经网络、LLM输出、训练LLM损失函数 卷积神经网络（CNN） 11, 77, 103

- 深度学习 1, 9, 11, 14, 19, 30, 47, 52, 108, 可计算性 47, 49, 53

- 交叉熵损失 50

- 定义与目的 47–48

- 激励不匹配 51, 55

- 用于LLM（下一词预测） 54–57

- 平滑性 47–48, 50

- 特异性 47–48 123, 146, 149

- 受人类大脑启发 9, 31, 46

- 长短期记忆网络（LSTM） 11

- 循环神经网络（RNN） 55, 63, 77

- 训练 46–54, 77

- 另见 应用（LLM）、深度学习（DL）、错误（LLM）、输入（LLM）、学习、机器学习（ML）、LLM输出、训练LLM

- 归一化（文本） M 机器学习（ML）

<details>
<summary>英文原文</summary>

in AI/ML context 8, 46 few-shot learning 114–115 by LLMs vs. humans 8–9, 46, 108–111 reinforcement learning (RL) 48, 73–75, 78, 93 supervised learning 46, 71–73 training algorithms 46–54 See also applications (LLM), deep learning (DL), 115–116, 141, 154 definition of 3–4 designing solutions with 125–139 efficiency (power, latency, refinement) 115–117 errors (LLM), inputs (LLM), machine learning (ML), neural networks, output from LLMs, training LLMs loss function convolutional neural networks (CNNs) 11, 77, 103 deep learning 1, 9, 11, 14, 19, 30, 47, 52, 108, computability 47, 49, 53 cross-entropy loss 50 definition and purpose 47–48 incentive mismatch 51, 55 for LLMs (next-token prediction) 54–57 smoothness 47–48, 50 specificity 47–48 123, 146, 149 inspiration from human brain 9, 31, 46 long short-term memory (LSTM) networks 11 recurrent neural networks (RNNs) 55, 63, 77 training of 46–54, 77 See also applications (LLM), deep learning (DL), errors (LLM), inputs (LLM), learning, machine learning (ML), output from LLMs, training LLMs normalization (text) M machine learning (ML)

</details>

- 控制词汇量大小 18–20

- 同形字 24

- 数字的 26, 99

- 分词过程中 17, 19–20

- 另见 同形字、语言、自然语言处理（NLP）、分词、词汇、单词

- 数字错误（LLM）、输入（LLM）、学习、

- 神经网络、LLM输出、

- 训练LLM

- 数学 LLM 理解 26, 97–99

- 在LLM中的表示 26, 97–99

- 分词 26, 97–99

- 另见 数学 计算机代数系统 (CAS) 99–100

- 形式化和符号化 95–96, 99–100

- Lean 编程语言用于证明 100, 114

- LLM 与 26, 58–59, 89, 95–100, 106, 114

- 数字表示 26, 97–99

- 分词 26, 89, 96–99, 106

- 另见 数字

- Modula-3

- 编程语言 58–59, 70, 88

- O OpenAI ChatGPT 1–2, 6–7, 10, 12, 15, 22, 24–26, 30, 43, 57–61, 66–70, 74, 90–93, 107, 109, 120–130, 143, 157, 160

- DALL-E 7, 152

- GPT 模型 2–7, 10, 15, 18, 22–26, 60, 70, 83, 另见 Lean 编程语言、Python 编程语言、源代码

- 多模态模型 98, 109, 123, 143, 152, 156

- tiktoken 22

- 另见 人工智能（AI）、聊天机器人、定义 22, 104

- 示例（图像和文本）22, 104–105

- ChatGPT、生成式AI、大语言模型（LLM）

- LLM输出 N 变更/约束 65–86, 156–160

- 自回归生成 40, 60, 62

- 偏差 55, 140, 142–143, 156–157

- 代码 89, 92–95, 100

- 创造性 vs. 主题性 43–44

- 解码/解嵌入 32–33, 40–42, 89, 101, 自然语言处理（NLP）历史 3

- 与LLM的关系 3–4

- 另见 同形字、语言、标准化（文本）、分词、词汇、单词

- 神经网络 103

- 结束序列（EoS）标记 41

- 伦理关注 12, 140, 156–160

- 架构（层）9, 31–32, 37–38, 40, 44, 101–104

- 格式要求 70, 81, 86

- 生成循环 40–41

- 图像 101, 103–105

- 许可影响 157–158

- 采样标记 33, 41–43

- 温度设置 43–44

- 另见 应用（LLM）、深度学习（DL）、使用DSPy 84–86

- 奖励函数质量奖励在RLHF 76–78

- 强化学习中 48, 73–74, 76–79

- 相似性奖励在RLHF 78 S 错误（LLM）、输入（LLM）、学习、

- 机器学习（ML）、神经网络、

- 训练LLM 自我改进（LLM）限制 111–112, 122, 147, 149

- 理论可能性 111, 147

- 语义空间 P 定义 35–36

- 内部关系 36

- 源代码补丁（图像）组合 101, 103–104

- 提取 101–103

- 替换视觉标记 89, 101–102

- LLM预训练 58–59, 88–95, 106, 120, 123, 142

- 代码分词 89–92

- 生成代码验证 92–95, 100

- 另见 Lean 编程语言、Modula-3

- 基础模型 66, 68, 72

- 定义 2, 10, 66

- 提示 编程语言、Python 编程语言

- 语音转文本 4, 125, 132, 134–135, 141

- 链式思维（CoT）119, 122

- 工程 62, 67, 84, 114, 117, 122

- 少样本学习 114–115

- 图像生成 105

- 指令遵循 6, 58, 60, 62, 67, 70–71, 另见 文本转语音

- 随机梯度下降（SGD）53–54

- 子词 74, 105, 114, 119

- 公共领域创建 使用BPE 20–22

- 定义 16

- 分词角色 16, 18, 20–22

- 监督微调（SFT）使用挑战 155–156

- 定义 155

- 另见 内容创作、版权、伦理、合理使用

- Python 编程语言 20, 58, 70, 81, 88, 数据要求 71–72

- 机制 72

- 陷阱（灾难性遗忘）72–73

- 目的 71 90–91, 93

- 另见 Lean 编程语言、Modula-3

- 编程语言、源代码 T R 技术 强化学习来自人类反馈 采纳与影响 1–2, 6, 107, 140–142,

<details>
<summary>英文原文</summary>

for controlling vocabulary size 18–20 for homoglyphs 24 of numbers 26, 99 in tokenization process 17, 19–20 See also homoglyphs, language, natural language processing (NLP), tokens and tokenization, vocabulary, words numbers errors (LLM), inputs (LLM), learning, neural networks, output from LLMs, training LLMs mathematics LLM understanding of 26, 97–99 representation in LLMs 26, 97–99 tokenization of 26, 97–99 See also mathematics computer algebra systems (CAS) 99–100 formal and symbolic 95–96, 99–100 Lean programming language for proofs 100, 114 LLMs and 26, 58–59, 89, 95–100, 106, 114 number representation 26, 97–99 tokenization for 26, 89, 96–99, 106 See also numbers Modula-3 programming language 58–59, 70, 88 O OpenAI ChatGPT 1–2, 6–7, 10, 12, 15, 22, 24–26, 30, 43, 57–61, 66–70, 74, 90–93, 107, 109, 120–130, 143, 157, 160 DALL-E 7, 152 GPT models 2–7, 10, 15, 18, 22–26, 60, 70, 83, See also Lean programming language, Python programming language, source code multimodal models 98, 109, 123, 143, 152, 156 tiktoken 22 See also artificial intelligence (AI), chatbots, definition of 22, 104 examples of (image and text) 22, 104–105 ChatGPT, generative AI, large language models (LLMs) output from LLMs N altering/constraining 65–86, 156–160 autoregressive generation 40, 60, 62 bias in 55, 140, 142–143, 156–157 for code 89, 92–95, 100 creativity vs. topicality 43–44 decoding/unembedding 32–33, 40–42, 89, 101, natural language processing (NLP) history of 3 relationship to LLMs 3–4 See also homoglyphs, language, normalization (text), tokens and tokenization, vocabulary, words neural networks 103 end of sequence (EoS) token 41 ethical concerns with 12, 140, 156–160 architecture (layers) 9, 31–32, 37–38, 40, 44, 101–104 formatting requirements 70, 81, 86 generation loop 40–41 for images 101, 103–105 licensing implications 157–158 sampling tokens 33, 41–43 temperature setting 43–44 See also applications (LLM), deep learning (DL), using DSPy for 84–86 reward function quality reward in RLHF 76–78 in reinforcement learning 48, 73–74, 76–79 similarity reward in RLHF 78 S errors (LLM), inputs (LLM), learning, machine learning (ML), neural networks, training LLMs self-improvement (LLM) limitations of 111–112, 122, 147, 149 theoretical possibility of 111, 147 semantic space P definition of 35–36 relationships within 36 source code patches (for images) combining 101, 103–104 extracting 101–103 replacing tokens for vision 89, 101–102 pretraining LLMs for 58–59, 88–95, 106, 120, 123, 142 tokenization of 89–92 validation of generated code 92–95, 100 See also Lean programming language, Modula-3 of base models 66, 68, 72 definition of 2, 10, 66 prompting programming language, Python programming language speech-to-text 4, 125, 132, 134–135, 141 chain-of-thought (CoT) 119, 122 engineering 62, 67, 84, 114, 117, 122 few-shot learning 114–115 for image generation 105 for instruction following 6, 58, 60, 62, 67, 70–71, See also text-to-speech stochastic gradient descent (SGD) 53–54 subwords 74, 105, 114, 119 public domain creation using BPE 20–22 definition of 16 role in tokenization 16, 18, 20–22 supervised fine-tuning (SFT) challenges with using 155–156 definition of 155 See also content creation, copyright, ethics, fair use Python programming language 20, 58, 70, 81, 88, data requirements 71–72 mechanics of 72 pitfalls (catastrophic forgetting) 72–73 purpose of 71 90–91, 93 See also Lean programming language, Modula-3 programming language, source code T R technology reinforcement learning from human feedback adoption and impact 1–2, 6, 107, 140–142,

</details>

- 144–146, 151, 160–161

- 双重用途 151

- 呈现与信任 126, 136–138

- 文本到语音 4, 125, 132, 134–135, 141

- 另见 语音到文本

- 词元与分词 字节对编码（BPE） 20–23, 90–91, 98

- 用于代码 89–92, 94–95

- 控制词汇表大小 18–20

- 优点 82

- 上下文大小考虑 83–84

- 过程 82–83

- 转换为向量（嵌入） 29, 31–38, 注意力机制 38–40, 62, 109

- 用于计算机视觉 89, 101–106

- 仅解码器模型 30, 44

- 编码器-解码器模型 30–31

- 仅编码器模型 30

- 层 31–33, 37–40, 44, 89, 101–102

- 位置信息 33, 36–38, 44

- 查询、键和值 38–40, 44, 61 44, 99, 101–102, 132

- 解码/解嵌入 32–33, 40–42, 89, 101, 103

- 序列结束（EoS）标记 41

- 同形字 23–24

- 用于图像（块） 89, 101–102, 106

- 语言公平性 26–27

- 用于数学 26, 89, 96–99, 106

- 归一化 14, 17, 19–20, 24, 45, 99

- 文本的数值表示 14–16, 30, 33–34

- 词表外问题 18

- 过程 14, 16–18, 20–23

- 风险 22–24

- 输出采样 33, 41–43, 101

- 分割 17, 20

- 特殊标记 21–22, 41, 95

- 子词 16, 18, 20–22, 32, 45

- 词汇表 15, 18–20, 22–23, 26, 41, 45, 80, 101, U 用户体验（UX） 聊天机器人及 68, 126, 132, 134

- 可解释AI与信任 136–137

- 透明度与对齐 137–138 V 向量 维度 35

- 作为嵌入 33–38, 40, 42, 62, 70, 89, 99, 109

- 另见 字节对编码（BPE）、同形字 101–104, 126, 132–134

- 用于图像块 102–103

- 表示词元 33–36

- 词汇表 语言、自然语言处理（NLP）、归一化（文本）、词汇表、词

- 训练LLM 控制大小 18–20, 22–23

- 定义 18

- 词表外问题 18

- 在分词中 15, 18, 22

- 另见 同形字、语言、自然语言 数据 3, 6, 10, 18, 21, 23, 26, 36, 46, 51, 54–57, 60–61, 66–69, 72, 78–80, 92–93, 96, 100, 105–114, 117, 120–125, 140–146, 150–160

- 微调 65–79, 93, 108, 110, 114–115, 117, 123, 128, 130, 145–146, 150, 153–154

- 梯度下降 46–54, 60, 72, 101, 108, 115

- 损失/奖励函数 46–55, 58, 73–74, 76–79

- 预训练 2, 10, 66, 68, 72

- 自我改进的局限性 111–114, 122, 147, 处理（NLP）、归一化（文本）、词元与分词、词 W 词 149

- 另见 应用（LLM）、深度学习（DL）、游戏与LLM 25, 45

- 由词元和子词表示 15–16, 错误（LLM）、输入（LLM）、学习、机器学习（ML）、神经网络、LLM输出

- Transformer模型 18, 20–22

- 语义关系 32, 34–36

- 另见 同形字、语言、自然语言处理（NLP）、归一化（文本）、词元与分词、词汇表 架构 30–33, 89, 101–102

<details>
<summary>英文原文</summary>

144–146, 151, 160–161 dual-use 151 presentation and trust 126, 136–138 text-to-speech 4, 125, 132, 134–135, 141 See also speech-to-text tokens and tokenization byte pair encoding (BPE) 20–23, 90–91, 98 for code 89–92, 94–95 controlling vocabulary size 18–20 benefits of 82 context size considerations 83–84 process of 82–83 conversion to vectors (embeddings) 29, 31–38, attention mechanism in 38–40, 62, 109 for computer vision 89, 101–106 decoder-only models 30, 44 encoder-decoder models 30–31 encoder-only models 30 layers of 31–33, 37–40, 44, 89, 101–102 positional information 33, 36–38, 44 queries, keys, and values 38–40, 44, 61 44, 99, 101–102, 132 decoding/unembedding 32–33, 40–42, 89, 101, 103 end of sequence (EoS) token 41 homoglyphs 23–24 for images (patches) 89, 101–102, 106 language equity and 26–27 for mathematics 26, 89, 96–99, 106 normalization in 14, 17, 19–20, 24, 45, 99 numeric representation of text 14–16, 30, 33–34 out-of-vocabulary problem 18 process of 14, 16–18, 20–23 risks of 22–24 sampling for output 33, 41–43, 101 segmentation in 17, 20 special tokens 21–22, 41, 95 subwords 16, 18, 20–22, 32, 45 vocabulary 15, 18–20, 22–23, 26, 41, 45, 80, 101, U user experience (UX) chatbots and 68, 126, 132, 134 explainable AI and trust 136–137 transparency and alignment of 137–138 V vectors dimensions of 35 as embeddings 33–38, 40, 42, 62, 70, 89, 99, 109 See also byte pair encoding (BPE), homoglyphs, 101–104, 126, 132–134 for image patches 102–103 representing tokens 33–36 vocabulary language, natural language processing (NLP), normalization (text), vocabulary, words training LLMs controlling size of 18–20, 22–23 definition of 18 out-of-vocabulary problem 18 in tokenization 15, 18, 22 See also homoglyphs, language, natural language data for 3, 6, 10, 18, 21, 23, 26, 36, 46, 51, 54–57, 60–61, 66–69, 72, 78–80, 92–93, 96, 100, 105–114, 117, 120–125, 140–146, 150–160 fine-tuning 65–79, 93, 108, 110, 114–115, 117, 123, 128, 130, 145–146, 150, 153–154 gradient descent in 46–54, 60, 72, 101, 108, 115 loss/reward functions 46–55, 58, 73–74, 76–79 pretraining 2, 10, 66, 68, 72 self-improvement limitations 111–114, 122, 147, processing (NLP), normalization (text), tokens and tokenization, words W words 149 See also applications (LLM), deep learning (DL), games and LLMs 25, 45 representation by tokens and subwords 15–16, errors (LLM), inputs (LLM), learning, machine learning (ML), neural networks, output from LLMs transformer model 18, 20–22 semantic relationships between 32, 34–36 See also homoglyphs, language, natural language processing (NLP), normalization (text), tokens and tokenization, vocabulary architecture 30–33, 89, 101–102

</details>

- 生成式AI是接收一些输入（数字、文本、图像）并产生新输出（通常是文本或图像）的技术。输入和输出的任何组合都是可能的，输出的性质取决于算法训练的目标。它可以用于添加细节、缩短改写、外推缺失部分等。

<details>
<summary>英文原文</summary>

Generative AI is about taking some input (numbers, text, images) and producing a new output (usually text or images). Any combination of input and output options is possible, and the nature of the output depends on what the algorithm was trained for. It could be to add detail, rewrite something to be shorter, extrapolate missing portions, and more.

</details>

- 生成式AI中各类术语及其关系的高层次图谱。生成式AI是对功能性的描述：即生成内容的功能，并利用AI技术来实现这一目标。

<details>
<summary>英文原文</summary>

A high-level map of various terms used in Generative AI and their relationships. Generative AI is a descrip-tion of functionality: the function of generating content and using techniques from AI to accomplish that goal.

</details>

- Python/数据

<details>
<summary>英文原文</summary>

PYTHON/DATA

</details>

- “如果你想真正理解LLM的工作原理，这是必读之作。”——Janelle Shane，aiweirdness.com

<details>
<summary>英文原文</summary>

“ Essential reading if you want to understand how LLMs really work.” —Janelle Shane, aiweirdness.com

</details>

### 大型语言模型的工作原理

Raff、Farris、Biderman（博思艾伦汉密尔顿公司）大型语言模型让“AI”名副其实。通过连接数十亿文档中的词语、概念和模式，LLM 能够生成我们期望从 ChatGPT、Claude 和 Deep-Seek 等工具中获得的类人回复。在这本兼具信息量与趣味性的书中，来自博思艾伦汉密尔顿公司（Booz Allen Hamilton）的世界顶级机器学习研究人员探讨了 LLM 的基础概念、机遇与局限，以及将 AI 融入组织与应用程序的最佳实践。

<details>
<summary>英文原文</summary>

Raff, Farris, Biderman for Booz Allen Hamilton L

arge Language Models put the “I” in “AI.” By connecting words, concepts, and patterns from billions of documents, LLMs are able to generate the human-like responses we’ve come to expect from tools like ChatGPT, Claude, and Deep-Seek. In this informative and entertaining book, the world’s best machine learning researchers from Booz Allen Hamilton explore foundational concepts of LLMs, their opportunities and limitations, and the best practices for incorporating AI into your organizations and applications.

</details>

“揭秘了革新人与机器交互的技术。”——苏达尔山·图姆昆塔，Meta

<details>
<summary>英文原文</summary>

“ Demystifi es technology revolutionizing human-machine interaction.” —Sudharshan Tumkunta, Meta “

</details>

### "An excellent no-nonsense introduction to LLMs."
—Kartik Dutta, Cisco

### 大型语言模型如何

本书将带你深入大语言模型内部，逐步展示一个自然语言提示词如何变为清晰可读的文本补全。本书以通俗易懂的语言，讲述大语言模型是如何创建的、为什么会出错，以及如何设计可靠的AI解决方案。在此过程中，你将了解大语言模型如何“思考”，如何设计基于大语言模型的应用（如智能体和问答系统），以及如何应对伦理、法律和安全方面的挑战。

<details>
<summary>英文原文</summary>

Work takes you inside an LLM, showing step-by-step how a natural language prompt becomes a clear, readable text completion. Written in plain language, you’ll learn how LLMs are created, why they make errors, and how you can design reliable AI solutions. Along the way, you’ll learn how LLMs “think,” how to design LLM-powered appli-cations like agents and Q&A systems, and how to navigate the ethical, legal, and security issues.

</details>

“在深度与清晰度之间实现了完美平衡，使其成为研究人员和实践者不可或缺的资源。”
——Mattia Zoccarato
Chiron AI

<details>
<summary>英文原文</summary>

Strikes the perfect balance between depth and clarity, making it an invaluable resource for both researchers and practitioners.” —Mattia Zoccarato Chiron AI What’s Inside

</details>

- ● 为特定应用定制大语言模型

<details>
<summary>英文原文</summary>

● Customize LLMs for specifi c applications

</details>

- ● 降低不良输出和偏见风险

<details>
<summary>英文原文</summary>

● Reduce the risk of bad outputs and bias

</details>

- ● 消除关于大语言模型的迷思

<details>
<summary>英文原文</summary>

● Dispel myths about LLMs

</details>

- ● 超越语言处理，无需任何机器学习或人工智能系统知识。

<details>
<summary>英文原文</summary>

● Go beyond language processing No knowledge of ML or AI systems is required.

</details>

### 爱德华·拉夫、德鲁·法里斯与斯特拉

Biderman 是博思艾伦汉密尔顿公司的新兴AI总监、AI/ML研究总监以及机器学习研究员。

<details>
<summary>英文原文</summary>

Biderman are the Director of Emerging AI, Director of AI/ML Research, and machine learning researcher at Booz Allen Hamilton.

</details>

对于印刷版书籍的拥有者，所有数字格式均免费提供：https://www.manning.com/freebook ISBN-13: 978-1-63343-708-1

<details>
<summary>英文原文</summary>

For print book owners, all digital formats are free: https://www.manning.com/freebook ISBN-13: 978-1-63343-708-1

</details>



---

*How Large Language Models Work · 中英双语版 · 由 book-agent 翻译管线生成*
