import re

# def test1():
    # str_content = """
    # Most competitive neural sequence transduction models have an encoder-decoder structure [5, 2, 35]. Here, the encoder maps an input sequence of symbol representations $( x _ { 1 } , . . . , x _ { n } )$ to a sequence of continuous representations $\textbf { z } = ~ ( z _ { 1 } , . . . , z _ { n } )$ . Given z, the decoder then generates an output sequence $\left( y _ { 1 } , . . . , y _ { m } \right)$ of symbols one element at a time. At each step the model is auto-regressive [10], consuming the previously generated symbols as additional input when generating the next.
    #
    # ![](images/f05ba6a0a2e484dbd8bf2a4a4fa2597a69a3a076f5b03cf4e818b176b4049244.jpg)
    # Figure 1: The Transformer - model architecture.
    #
    # The Transformer follows this overall architecture using stacked self-attention and point-wise, fully connected layers for both the encoder and decoder, shown in the left and right halves of Figure 1, respectively.
    #
    # ## 3.1 Encoder and Decoder Stacks
    #     """
    #
    # re_str = re.compile(
    #     r"!\[.*?\]\(.*?" + re.escape("f05ba6a0a2e484dbd8bf2a4a4fa2597a69a3a076f5b03cf4e818b176b4049244.jpg") + "*?\).")
    # # print(re_str)
    # # str_replace = f"![hello](imgage_url)"
    # # new_str = re_str.sub(str_replace,str_content)
    # str_content = re_str.sub(lambda _: f"![hello](imgage_url)", str_content)
    #
    # print(str_content)


def test2():
    reg = re.compile(r"\!\[.*?\]\((.*?)\)")
    text = 'RRF是一种![ces](http://web.com/jpeg.jpeg)倒数排名融合算法\n![ces](http://web.com/33.png)'
    images = reg.findall(text)
    print(images)

    for image_data in images:
        print(image_data.endswith(('.png',".jpg",".jpeg",".gif")))


def test3():

    stripped_lines = """```
123
~~~abc````
```"""



    # 方法2: 保持原有的 match 方式，但更清晰
    print(stripped_lines.split("\n"))
    for stripped_line in stripped_lines.split("\n"):
        # code_block_marker_match = re.match(r'^(`{3,}|~{3,}).*?(`{3,}|~{3,}).*?(`{3,}|~{3,})$', stripped_line)
        print(stripped_line)
        code_block_marker_match = re.match(r'^(`{3,}|~{3,})$', stripped_line)
        if code_block_marker_match:
            marker = code_block_marker_match.group(1)
            print(f"匹配结果：{marker}")
        # if code_block_marker_match:
        #     marker1 = code_block_marker_match.group(1)
        #     marker2 = code_block_marker_match.group(2)
        #     marker3 = code_block_marker_match.group(3)
        #     print("\n原有方法结果:")
        #     print(f"标记1: {marker1}")
        #     print(f"标记2: {marker2}")
        #     print(f"标记3: {marker3}")
        # if stripped_line.startswith("```") or stripped_line.startswith("~~~"):
        #     print(stripped_line)


if __name__ == '__main__':
    test3()
