# Dispatch Rules - 多态分发点识别规则

本文件定义多态分发点（接口/抽象类有多个实现类）的识别规则和噪声过滤条件。

脚本 `phase1c_dispatch_detect.py` 读取本文件作为识别依据。

规则格式说明：
- 以 `## ` 开头的章节标题作为分类标识
- `- ` 开头的列表项为规则条目
- 脚本按行解析，只提取列表项内容

---

## noise-interface

以下接口即使有多个实现类也不算分发点（框架接口、标记接口）：

- Serializable
- Comparable
- Cloneable
- Runnable
- AutoCloseable
- Closeable
- Iterable
- Collection
- EventListener

## noise-interface-prefix

以下包前缀的接口不算分发点（JDK/框架标准接口）：

- java.io.
- java.lang.
- java.util.
- java.util.function.
- org.springframework.beans.factory.Aware
- org.springframework.context.ApplicationListener
- org.springframework.core.Ordered

## noise-interface-suffix

以下后缀的接口不算分发点（框架回调接口）：

- Aware
- Listener
- Callback

## noise-abstract-class

以下抽象类不算分发点：

- Object
- Enum
- Throwable
- ApplicationEvent
- HttpServlet

## noise-class-prefix

以下包前缀的类不算分发点：

- java.lang.
- java.io.
- javax.servlet.
- jakarta.servlet.
- org.springframework.context.

## exclude-package

以下包前缀的接口不算分发点（外部依赖/DTO/数据对象）。
**按项目实际情况在此处添加。**

<!-- 示例（取消注释并替换为实际包名）：
- com.example.external.
- com.example.dto.
- com.example.entity.
-->

## exclude-annotation

标注以下注解的接口不算分发点：

- @FeignClient
- @Mapper
- @Repository
- @MapperScan

## exclude-directory

以下目录中的实现类不算分发点：

- src/test

## min-implementations

接口/抽象类至少有多少个具体（非 abstract）实现类才算分发点：

- 2

## dispatch-detection

分发方式检测规则。脚本在 Context 类源码中搜索以下模式：

- STREAM_DISPATCH: .stream() 和 filter(
- MAP_DISPATCH: .get(key)
- SWITCH_DISPATCH: switch( 或 else if(
